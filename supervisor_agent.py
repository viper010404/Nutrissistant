import os
import json
from dotenv import load_dotenv
from pydantic import SecretStr

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

import schedule_agent
import workout_agent
import state_manager

# Load environment variables
load_dotenv()

LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
MODEL_NAME = "RPRTHPB-gpt-5-mini"
MODULE_NAME = "Supervisor" 

text_llm = ChatOpenAI(
    api_key=SecretStr(LLMOD_API_KEY or ""),
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME
)

# JSON LLM for strict data extraction 
json_llm = ChatOpenAI(
    api_key=SecretStr(LLMOD_API_KEY or ""),
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME,
).bind(response_format={"type": "json_object"})


def _parse_json_response_content(content):
    if isinstance(content, list):
        content = "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
        )
    return json.loads(content)


def _attach_slots_to_units(units, scheduled_slots):
    """Attach available schedule slots to routine units using day match then index fallback."""
    prepared_units = []
    free_slots = [slot for slot in scheduled_slots if isinstance(slot, dict)]

    for unit in units:
        if isinstance(unit, dict):
            unit_copy = dict(unit)
        else:
            unit_copy = {}
        unit_copy["scheduled_slots"] = []
        prepared_units.append(unit_copy)

    # Prefer day-label matching first.
    for unit in prepared_units:
        day_label = (unit.get("day") or unit.get("day_label") or "").strip().lower()
        if not day_label:
            continue
        match_idx = next(
            (
                idx for idx, slot in enumerate(free_slots)
                if str(slot.get("day", "")).strip().lower() == day_label
            ),
            None,
        )
        if match_idx is not None:
            unit["scheduled_slots"].append(free_slots.pop(match_idx))

    # Fill remaining units in order with remaining slots.
    for unit in prepared_units:
        if not free_slots:
            break
        if unit["scheduled_slots"]:
            continue
        unit["scheduled_slots"].append(free_slots.pop(0))

    return prepared_units


def get_user_data():
    """Fetches the current user data from the state manager."""
    return state_manager.load_state()

#TO DO: hard coded response structure
def analyze_intent_and_extract_metadata(user_query, user_profile, step_tracer):
    """Analyzes intent and extracts missing info and required tasks."""
    
    sys_prompt = """You are the Nutrissistant Supervisor. Analyze the user query against their profile.
    
    CRITICAL RULES FOR 'missing_info':
    1. MINIMIZE QUESTIONS: Only ask for absolute blockers.
    2. EQUIPMENT: Assume access to standard gym equipment if a gym is mentioned.
    3. DO NOT ASK FOR SCHEDULE: The Schedule Agent handles time.
    4. ASSUME DEFAULTS: Assume intermediate fitness, no injuries unless stated.

    Output JSON only with this schema:
    {
        "tasks": [], // Array of strings. MUST be from the allowed list below.
        "goals": ["extracted goal 1"],
        "missing_info": [] // Array of strings. Keep empty [] if rules above apply.
    }

    ALLOWED TASKS:
    - "PLAN_MEAL": Planning nutrition/meals.
    - "WORKOUT": Creating, extracting, or modifying workout plans.
    - "FIND_RECIPE": Searching for or extracting recipe details.
    - "SCHEDULE": Checking or modifying the user's schedule/calendar.
    - "GENERAL_QUESTION": Answering general fitness/nutrition questions.
    - "OTHER": Anything that doesn't fit the above.
    """
    user_prompt = f"Profile: {user_profile}\nQuery: {user_query}"

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]

    response = json_llm.invoke(messages)
    result = _parse_json_response_content(response.content)
    
    step_tracer.append({
        "module": MODULE_NAME,
        "prompt": {"system": sys_prompt, "user": user_prompt},
        "response": result
    })
    
    return result

def check_for_clarification(missing_info, step_tracer):
    """Generates a polite clarification question."""
    
    sys_prompt = "You are a helpful assistant. Ask the user politely to provide the following missing information. Be brief."
    user_prompt = f"Missing info needed: {', '.join(missing_info)}"
    
    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]

    # Call the standard text LangChain model
    response = text_llm.invoke(messages)
    clarification_message = response.content
    
    step_tracer.append({
        "module": MODULE_NAME,
        "prompt": {"system": sys_prompt, "user": user_prompt},
        "response": {"message": clarification_message}
    })
    
    return clarification_message

def _build_compact_state_summary(state):
    """Builds a compact, trimmed state bundle for the clarification gate LLM."""
    user_profile = state.get("user_profile", {})

    # Extract a small set of profile fields relevant to clarification
    if isinstance(user_profile, dict):
        profile_summary = {
            k: user_profile[k]
            for k in ("name", "age", "fitness_level", "goals", "injuries", "preferences", "equipment")
            if user_profile.get(k)
        }
    else:
        profile_summary = {"raw": str(user_profile)[:300]}

    # Current routine — compact unit list only
    workouts_state = state.get("workouts", {}) if isinstance(state.get("workouts"), dict) else {}
    current_routine_id = workouts_state.get("current_routine_id")
    routines = workouts_state.get("routines", []) if isinstance(workouts_state.get("routines"), list) else []
    current_routine = next(
        (r for r in routines if isinstance(r, dict) and r.get("id") == current_routine_id),
        None,
    )
    routine_summary = None
    if current_routine:
        compact_units = [
            {k: u.get(k) for k in ("focus", "day", "duration_mins") if u.get(k)}
            for u in (current_routine.get("units") or [])
            if isinstance(u, dict)
        ]
        routine_summary = {
            "name": current_routine.get("routine_name"),
            "goal": current_routine.get("goal"),
            "units": compact_units,
        }

    # Known hard constraints from profile
    known_constraints = {
        k: user_profile[k]
        for k in ("injuries", "equipment", "dietary_restrictions", "allergies")
        if isinstance(user_profile, dict) and user_profile.get(k)
    }

    return {
        "user_profile_summary": profile_summary,
        "current_routine_summary": routine_summary,
        "known_constraints": known_constraints,
        "system_defaults": {
            "fitness_level": "intermediate",
            "equipment": "standard gym",
            "workout_duration_mins": 60,
            "units_per_week": 3,
        },
    }


def assess_clarification_need(user_query, tasks, state, step_tracer):
    """Decides whether a single critical clarification question is needed before executing tasks.

    Returns a dict with:
      needs_clarification (bool)
      questions          (list[str], max 1 item)
      reason             (str)
      assumptions_if_skipped (list[str])
    """
    state_summary = _build_compact_state_summary(state)

    sys_prompt = """You are a smart gating system for a fitness assistant. Decide whether to ask the user ONE clarification question before proceeding.

RULES:
1. Use defaults when ambiguity is minor.
2. ASK AT MOST ONE QUESTION. Pick the single most critical one only.
3. Ask a question when ambiguity would materially change the output structure, make the result likely wrong, or create a safety risk.
4. For vague workout requests (for example "add a workout" or "change my routine"), it is appropriate to ask one disambiguation question about intent.
5. Do NOT ask for minor preferences or non-critical details.
6. NEVER ask about schedule — the system handles that separately.


Respond in JSON only:
{
  "needs_clarification": true | false,
  "questions": [],
  "reason": "brief explanation",
  "assumptions_if_skipped": []
}"""

    user_prompt = json.dumps(
        {"user_query": user_query, "detected_tasks": tasks, "context": state_summary},
        indent=2,
        default=str,
    )

    messages = [SystemMessage(content=sys_prompt), HumanMessage(content=user_prompt)]
    response = json_llm.invoke(messages)
    result = _parse_json_response_content(response.content)

    step_tracer.append({
        "module": MODULE_NAME,
        "prompt": {"system": sys_prompt, "user": user_prompt},
        "response": result,
    })

    return result


def validate_and_resolve_conflicts(nutrition_draft, fitness_draft, step_tracer):
    """Validates the generated plans for conflicts."""
    
    sys_prompt = """Compare the nutrition and fitness drafts. 
    Are there any critical conflicts? (e.g. heavy leg day with zero carb recovery meal).
    Return JSON: {"status": "ok" | "error", "reason": "...", "suggested_fix": "..."}"""
    user_prompt = f"Nutrition: {nutrition_draft}\nFitness: {fitness_draft}"

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]

    response = json_llm.invoke(messages)
    result = _parse_json_response_content(response.content)
    
    step_tracer.append({
        "module": MODULE_NAME,
        "prompt": {"system": sys_prompt, "user": user_prompt},
        "response": result
    })
    
    return result

# Order of Operations
TASK_PRIORITY = {
    "GENERAL_QUESTION": 1,
    "OTHER": 1,
    "FIND_RECIPE": 2,
    "SCHEDULE": 3,      
    "PLAN_MEAL": 4,    
    "WORKOUT": 4   
}

def orchestrate_workflow(user_query):
    """Main orchestration loop with dynamic constraint passing."""
    
    state = get_user_data()
    step_tracer = [] 
    responses = [] 
    routine_generated = False
    latest_routine_response = None
    routine_draft = None
    
    # This dictionary will pass constraints between agents in real-time
    shared_context = {
        "workout_time_limit_mins": None,
        "meal_prep_time_limit_mins": None,
        "other_event_limit_mins": None,
        "scheduled_slots": []
    }

    intent_data = analyze_intent_and_extract_metadata(user_query, state["user_profile"], step_tracer)
    
    missing_info = intent_data.get("missing_info", [])
    tasks = intent_data.get("tasks", [])

    # Backward compatibility: map legacy workout task labels into the single WORKOUT task.
    task_aliases = {
        "PLAN_WORKOUT": "WORKOUT",
        "EXTRACT_WORKOUT": "WORKOUT"
    }
    tasks = [task_aliases.get(t, t) for t in tasks if isinstance(t, str)]
    tasks = list(dict.fromkeys(tasks))

    # Smart clarification gate — runs after intent detection, before task execution
    clarification_result = assess_clarification_need(user_query, tasks, state, step_tracer)

    # Merge legacy missing_info items with smart-gate questions into one list
    gate_questions = clarification_result.get("questions", []) if isinstance(clarification_result, dict) else []
    combined_questions = list(missing_info) + [q for q in gate_questions if q not in missing_info]

    if combined_questions or clarification_result.get("needs_clarification"):
        # If the gate produced a ready-phrased question, use it directly;
        # otherwise fall back to re-phrasing the raw missing_info items.
        if gate_questions:
            clarification = gate_questions[0]
        else:
            clarification = check_for_clarification(combined_questions, step_tracer)

        state["status"] = "asking"
        state["missing_info"] = combined_questions
        state_manager.save_state(state)
        return {"response": clarification, "steps": step_tracer}

    # Sort tasks by the new priority
    tasks = sorted(tasks, key=lambda x: TASK_PRIORITY.get(x, 99))

    nutrition_draft = state.get("plan_drafts", {}).get("nutrition", None)
    fitness_draft = state.get("plan_drafts", {}).get("fitness", None)
    workouts_state = state.get("workouts", {}) if isinstance(state.get("workouts"), dict) else {}
    current_routine_id = workouts_state.get("current_routine_id")
    routines = workouts_state.get("routines", []) if isinstance(workouts_state.get("routines"), list) else []
    current_routine = next(
        (routine for routine in routines if isinstance(routine, dict) and routine.get("id") == current_routine_id),
        None,
    )

    # Execute Sub-Agents Sequentially with Context Sharing
    for task in tasks:
        
        if task == "SCHEDULE":
            # Pass the query, tracer, and shared context to the schedule agent
            schedule_result = schedule_agent.execute_schedule_task(
                user_query, 
                step_tracer, 
                shared_context
            )
            # Update the orchestrator's shared context with time constraints found
            shared_context = schedule_result["shared_context"]
            responses.append(schedule_result["response"])

        elif task == "WORKOUT":
            routine_result = workout_agent.execute_weekly_routine_task(
                user_query=user_query,
                shared_context=shared_context,
                step_tracer=step_tracer,
                current_routine=current_routine,
            )
            routine_generated = True
            routine_draft = routine_result.get("routine_draft", {})
            routine_units = routine_draft.get("units", []) if isinstance(routine_draft, dict) else []
            if routine_units and isinstance(routine_units[0], dict):
                first_draft = routine_units[0].get("draft")
                if isinstance(first_draft, dict):
                    fitness_draft = first_draft
            latest_routine_response = routine_result.get("response", "Prepared your weekly routine.")
            responses.append(latest_routine_response)

        elif task == "PLAN_MEAL":
            # Meal planner can also use constraints (e.g., if schedule only leaves 15 mins for cooking)
            prep_limit = shared_context.get("meal_prep_time_limit_mins", 30)
            
            nutrition_draft = f"Placeholder Nutrition Plan (Under {prep_limit} mins prep)"
            responses.append("Placeholder: Planned your meals.")
            
        elif task == "FIND_RECIPE":
            responses.append("Placeholder: Found your recipe.")
            
        elif task == "GENERAL_QUESTION":
            responses.append("Placeholder: Answered general question.")
            
        elif task == "OTHER":
            responses.append("Placeholder: Handled 'other' request.")

    state = state_manager.load_state()
    # Save final drafts and update status
    if "plan_drafts" not in state:
        state["plan_drafts"] = {}
    
    if nutrition_draft is not None:
        state["plan_drafts"]["nutrition"] = nutrition_draft
    if fitness_draft is not None:
        state["plan_drafts"]["fitness"] = fitness_draft

    state["user_query"] = user_query
    
    state["status"] = "idle"
    state_manager.save_state(state)

    if routine_generated and isinstance(routine_draft, dict):
        routine_units = routine_draft.get("units", []) if isinstance(routine_draft.get("units"), list) else []
        units_with_slots = _attach_slots_to_units(routine_units, shared_context.get("scheduled_slots", []))
        base_name = current_routine.get("routine_name", "Weekly Routine") if isinstance(current_routine, dict) else "Weekly Routine"
        routine_name = routine_draft.get("routine_name") or base_name
        state_manager.save_weekly_routine(
            routine_name=routine_name,
            goal=routine_draft.get("goal", "general fitness"),
            units=units_with_slots,
            source_query=user_query,
            response_text=latest_routine_response or "",
            source="agent",
        )

    final_response = "\n".join(responses)

    return {"response": final_response, "steps": step_tracer}