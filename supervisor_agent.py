import os
import json
from dotenv import load_dotenv
from pydantic import SecretStr
from datetime import datetime, timedelta, timezone

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
def analyze_intent_and_extract_metadata(user_query, state, step_tracer):
    """Analyzes intent, extracts missing info, and categorizes new persistent context."""
    # Build the 60-minute history block
    history_entries = state.get("chat_history", [])
    recent_history = []
    now = datetime.now(timezone.utc)
    
    for entry in history_entries:
        try:
            entry_time = datetime.fromisoformat(entry["timestamp"])
            if now - entry_time <= timedelta(minutes=60):
                role = entry.get("role", "unknown").capitalize()
                content = entry.get("content", "")
                recent_history.append(f"{role}: {content}")
        except Exception:
            continue
            
    history_text = "\n".join(recent_history[-10:]) if recent_history else "No recent history."
    # Check if we were previously asking a clarification question
    pending_info = state.get("missing_info", []) if state.get("status") == "asking" else []
    
    sys_prompt = f"""You are the Nutrissistant Supervisor. Analyze the user query.
    
    --- CURRENT KNOWN CONTEXT ---
    Profile: {state.get('user_profile', '')}
    Equipment: {state.get('equipment', [])}
    Injuries: {state.get('injuries', [])}
    Allergies: {state.get('allergies', [])}
    Dietary Restrictions: {state.get('dietary_restrictions', [])}
    Workout Preferences: {state.get('general_workout_restrictions', [])}
    Meal Preferences: {state.get('general_meal_restrictions', [])}
    Pending Info Requested from User: {pending_info}

    --- RECENT CONVERSATION HISTORY (Last 60 mins) ---
    {history_text}
    -----------------------------
    
    CRITICAL RULES:
    1. RESOLVE CONTEXT (COREFERENCE): If the user's query relies on previous context (e.g., using pronouns like "these", "it", "that plan"), use the Recent Conversation History to figure out exactly what they mean. 
    2. REWRITE QUERY (CONTINUATION & MERGING): Output a `resolved_query` that makes the user's intent completely explicit and standalone. 
       - If the user is providing missing information to a previous request (e.g., "I have 2kg dumbbells"), you MUST combine it with their original goal from the history. 
       - If they say "remove these", rewrite as "Remove the workouts we just scheduled".
    3. EXTRACT NEW CONTEXT: Categorize new persistent info into `extracted_context`.
    4. MINIMIZE QUESTIONS: Only ask absolutely necessary questions for the task.
       - For WORKOUT generation: Check if Target workouts/week, Preferred time, and Equipment are known.
       - For SCHEDULE tasks: DO NOT ask for the target day or time if the user leaves them out. The scheduling system is autonomous and will automatically find the next open slot. NEVER add day or time to `missing_info` for a scheduling request.
    5. AVOID RE-ASKING: Do NOT ask for information already in the 'CURRENT KNOWN CONTEXT'.
    6. INFER CONTINUATION: If the user is answering a question, infer the task from the 'Pending Info'.
    7. STRICT SCHEDULING SEPARATION: If the user's query is ONLY about moving, rescheduling, or removing an existing event on the calendar (e.g., "move my workout to 5pm", "cancel tomorrow's meal", "reschedule the run"), output ONLY the "SCHEDULE" task. Do NOT output "WORKOUT" or "PLAN_MEAL" unless they explicitly want to change the exercises or recipes.

    Output JSON only with this schema:
    {{
        "tasks": [], // MUST use ONLY the allowed tasks below.
        "goals": [],
        "missing_info": [],
        "extracted_context": {{
            "equipment": [],
            "injuries": [],
            "allergies": [],
            "dietary_restrictions": [],
            "general_workout_restrictions": [],
            "general_meal_restrictions": []
        }}
    }}
    
    ALLOWED TASKS:
    - "SCHEDULE": Moving, removing, booking, or checking availability on the calendar.
    - "WORKOUT": Generating new routines or changing the actual exercises/content of a workout.
    - "PLAN_MEAL": Generating new meals or changing the actual food/recipes.
    - "FIND_RECIPE": Searching for or extracting recipe details.
    - "OTHER": Anything that doesn't fit the above.
    """
    
    user_prompt = f"Query: {user_query}"

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


def _select_workout_pipeline(user_query, current_routine, step_tracer):
    """Uses a lightweight LLM decision to pick workout pipeline mode."""
    sys_prompt = """You are a routing assistant for workout generation.
Choose exactly one pipeline mode and return JSON only:
{
    "pipeline": "reflection|simple_rag",
  "reason": "short reason"
}

Selection policy:
1. Choose "reflection" when the request is for a full new routine, broad redesign, or substantial restructuring.
2. Choose "simple_rag" when the request is a small adjustment (swap/replace/add/remove/modify one part).
3. Use current_routine context if available.
4. Be decisive: always return one mode.
"""

    compact_routine = None
    if isinstance(current_routine, dict):
        units = current_routine.get("units")
        compact_units = []
        if isinstance(units, list):
            for unit in units[:7]:
                if isinstance(unit, dict):
                    compact_units.append({
                        "title": unit.get("title"),
                        "focus_type": unit.get("focus_type") or unit.get("focus"),
                        "day_label": unit.get("day_label") or unit.get("day"),
                        "duration_limit_mins": unit.get("duration_limit_mins") or unit.get("duration_mins"),
                    })
        compact_routine = {
            "routine_name": current_routine.get("routine_name"),
            "goal": current_routine.get("goal"),
            "units": compact_units,
        }

    user_prompt = json.dumps({
        "query": user_query,
        "current_routine": compact_routine,
    })

    try:
        response = json_llm.invoke([
            SystemMessage(content=sys_prompt),
            HumanMessage(content=user_prompt)
        ])
        result = _parse_json_response_content(response.content)
        raw_pipeline = (result.get("pipeline") or "").strip().lower()
        pipeline = raw_pipeline if raw_pipeline in ("reflection", "simple_rag") else "simple_rag"

        step_tracer.append({
            "module": MODULE_NAME,
            "stage": "workout_pipeline_selection",
            "prompt": {"system": sys_prompt, "user": user_prompt},
            "response": {
                "pipeline": pipeline,
                "raw_pipeline": raw_pipeline,
                "reason": result.get("reason", "")
            }
        })
        return pipeline
    except Exception as e:
        fallback = "reflection" if not isinstance(current_routine, dict) else "simple_rag"
        step_tracer.append({
            "module": MODULE_NAME,
            "stage": "workout_pipeline_selection_fallback",
            "prompt": {"system": sys_prompt, "user": user_prompt},
            "response": {
                "pipeline": fallback,
                "reason": "selector_failed",
                "error": str(e)
            }
        })
        return fallback

def orchestrate_workflow(user_query):
    """Main orchestration loop with multi-phase execution."""
    
    state = get_user_data()
    step_tracer = [] 
    responses = [] 

    # Log the user's raw input right away
    if "chat_history" not in state:
        state["chat_history"] = []
    
    state["chat_history"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "user",
        "content": user_query
    })
    
    routine_generated = False
    latest_routine_response = None
    routine_draft = None
    
    shared_context = {
        "workout_time_limit_mins": None,
        "meal_prep_time_limit_mins": None,
        "other_event_limit_mins": None,
        "scheduled_slots": []
    }

    intent_data = analyze_intent_and_extract_metadata(user_query, state, step_tracer)

    # OVERRIDE THE QUERY WITH THE RESOLVED VERSION
    effective_query = intent_data.get("resolved_query", user_query)
    
    # SAVE EXTRACTED CONTEXT 
    extracted = intent_data.get("extracted_context", {})
    context_keys = [
        "equipment", "injuries", "allergies", "dietary_restrictions", 
        "general_workout_restrictions", "general_meal_restrictions"
    ]
    context_changed = False
    
    for key in context_keys:
        if key in extracted and isinstance(extracted[key], list):
            for item in extracted[key]:
                # Append only if it's not already in the list
                if item not in state.get(key, []):
                    state[key].append(item)
                    context_changed = True
                    
    if context_changed:
        state_manager.save_state(state)
        # Update our local reference to pass down to other agents if needed
        shared_context["updated_user_context"] = {k: state[k] for k in context_keys}

    missing_info = intent_data.get("missing_info", [])
    
    # Normalize tasks
    raw_tasks = intent_data.get("tasks", [])
    task_aliases = {"PLAN_WORKOUT": "WORKOUT", "EXTRACT_WORKOUT": "WORKOUT"}
    tasks = set(task_aliases.get(t, t) for t in raw_tasks if isinstance(t, str))

    # --- AUTONOMIC BEHAVIOR TRIGGER ---
    if "WORKOUT" in tasks or "PLAN_MEAL" in tasks:
        tasks.add("SCHEDULE")

    # Handle missing info first
    if missing_info:
        clarification = check_for_clarification(missing_info, step_tracer)
        state["status"] = "asking"
        state["missing_info"] = missing_info
        state_manager.save_state(state)
        return {"response": clarification, "steps": step_tracer}
    elif state.get("status") == "asking":
        # If we were asking, and now missing_info is empty, the user answered!
        # Clear the asking status so we don't get stuck.
        state["status"] = "idle"
        state["missing_info"] = []
        state_manager.save_state(state)

    # Extract current state data (Rest of your existing function continues here...)
    nutrition_draft = state.get("plan_drafts", {}).get("nutrition", None)
    fitness_draft = state.get("plan_drafts", {}).get("fitness", None)
    workouts_state = state.get("workouts", {}) if isinstance(state.get("workouts"), dict) else {}
    current_routine_id = workouts_state.get("current_routine_id")
    routines = workouts_state.get("routines", []) if isinstance(workouts_state.get("routines"), list) else []
    current_routine = next(
        (r for r in routines if isinstance(r, dict) and r.get("id") == current_routine_id), None
    )

    # ==========================================
    # PHASE 1: GATHER CONSTRAINTS (Read)
    # ==========================================
    if "SCHEDULE" in tasks:
        # Check if we are generating something new (Workout, Meal) or JUST scheduling
        is_generation_planned = "WORKOUT" in tasks or "PLAN_MEAL" in tasks
        
        schedule_result = schedule_agent.execute_schedule_task(
            user_query=effective_query, 
            step_tracer=step_tracer, 
            shared_context=shared_context,
            mode="gather_constraints" if is_generation_planned else "execute_full"
        )
        shared_context = schedule_result.get("shared_context", shared_context)
        
        # Only output schedule responses if it's relevant (skip silence on background gathering)
        schedule_resp = schedule_result.get("response", "").strip()
        if schedule_resp:
            # Only hide the default response if we are secretly gathering slots for a workout generator
            if is_generation_planned and schedule_resp == "Checked schedule constraints.":
                pass 
            elif schedule_resp == "Checked schedule constraints.":
                responses.append("I couldn't identify the specific calendar action. Could you clarify the event and time?")
            else:
                responses.append(schedule_resp)

    # ==========================================
    # PHASE 2: GENERATION 
    # ==========================================
    if "WORKOUT" in tasks:
        selected_pipeline = _select_workout_pipeline(effective_query, current_routine, step_tracer)
        shared_context["workout_pipeline"] = selected_pipeline

        routine_result = workout_agent.execute_weekly_routine_task(
            user_query=effective_query,
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

    if "PLAN_MEAL" in tasks:
        prep_limit = shared_context.get("meal_prep_time_limit_mins", 30)
        nutrition_draft = f"Placeholder Nutrition Plan (Under {prep_limit} mins prep)"
        responses.append("Placeholder: Planned your meals.")
        
    if "FIND_RECIPE" in tasks:
        responses.append("Placeholder: Found your recipe.")
        
    if "OTHER" in tasks:
        responses.append("I'm sorry, I can't help with that.")

    # ==========================================
    # PHASE 3: COMMIT & SYNC (Write)
    # ==========================================
    if routine_generated and isinstance(routine_draft, dict):
        routine_units = routine_draft.get("units", []) if isinstance(routine_draft.get("units"), list) else []
        
        # Supervisor maps the free slots gathered in Phase 1 to the generated units
        units_with_slots = _attach_slots_to_units(routine_units, shared_context.get("scheduled_slots", []))
        
        base_name = current_routine.get("routine_name", "Weekly Routine") if isinstance(current_routine, dict) else "Weekly Routine"
        routine_name = routine_draft.get("routine_name") or base_name
        
        # Save to state
        state_manager.save_weekly_routine(
            routine_name=routine_name,
            goal=routine_draft.get("goal", "general fitness"),
            units=units_with_slots,
            source_query=user_query,
            response_text=latest_routine_response or "",
            source="agent",
        )
        
        # Autonomically lock the new units into the calendar
        if "SCHEDULE" in tasks:
            schedule_agent.commit_routine_to_calendar(units_with_slots, step_tracer)
            responses.append("I have successfully added these to your calendar.")

    # Update global state plan drafts
    state = state_manager.load_state()
    if "plan_drafts" not in state:
        state["plan_drafts"] = {}
    if nutrition_draft is not None:
        state["plan_drafts"]["nutrition"] = nutrition_draft
    if fitness_draft is not None:
        state["plan_drafts"]["fitness"] = fitness_draft

    state["user_query"] = effective_query
    state["status"] = "idle"

    final_response = "\n\n".join(filter(None, responses))

    if not final_response:
        final_response = "I'm sorry, I couldn't find an action to take based on that. Could you rephrase what you'd like to do?"


    # Log the agent's response
    state["chat_history"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "agent",
        "content": final_response
    })
    state_manager.save_state(state)

    return {"response": final_response, "steps": step_tracer}