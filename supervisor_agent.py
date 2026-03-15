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
from src.meal_planner import meal_planner_agent
from src.recipe_extractor.main import run_recipe_extractor

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
    """Attach available schedule slots to routine units. Modifies scheduled_slots in-place."""
    prepared_units = []
    
    for unit in units:
        if isinstance(unit, dict):
            unit_copy = dict(unit)
        else:
            unit_copy = {}
        unit_copy["scheduled_slots"] = []
        prepared_units.append(unit_copy)

    # First pass: try to match by exact day label
    for unit in prepared_units:
        day_label = (unit.get("day") or unit.get("day_label") or "").strip().lower()
        if not day_label:
            continue
            
        match_idx = next(
            (idx for idx, slot in enumerate(scheduled_slots) 
             if isinstance(slot, dict) and str(slot.get("day", "")).strip().lower() == day_label),
            None,
        )
        if match_idx is not None:
            unit["scheduled_slots"].append(scheduled_slots.pop(match_idx))

    # Second pass: fill remaining units with any remaining slots (AVOIDING meal slots!)
    for unit in prepared_units:
        if unit["scheduled_slots"]:
            continue
        
        match_idx = next(
            (idx for idx, slot in enumerate(scheduled_slots) 
             if isinstance(slot, dict) and not any(kw in str(slot.get("event", "")).lower() for kw in ["meal", "dinner", "lunch", "breakfast", "recipe"])),
            None,
        )
        if match_idx is not None:
            unit["scheduled_slots"].append(scheduled_slots.pop(match_idx))

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
    3. EXTRACT NEW CONTEXT: Categorize new persistent info into `extracted_context`. If the user provides new details that conflict with the current, override the old and write the new.
       - `general_meal_restrictions`: ONLY use this for actual food items, flavors, or ingredients the user dislikes/avoids (e.g., "no mushrooms", "I hate spicy food"). DO NOT put time limits (e.g., "40 minutes") or meal types (e.g., "lunch") here.
    4. MINIMIZE QUESTIONS: Only ask absolutely necessary questions for the task.
       - For WORKOUT generation: Check if Target workouts/week, Preferred time, and Equipment are known (or it could be just one workout).
       - For RECIPE or MEAL generation: If the user provides NO specific constraints (like a preferred cuisine, specific craving, or max prep time), add "preferred cuisine or maximum prep time" to `missing_info`.
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
    - "PLAN_MEAL": Generating meal plans for whole days/weeks, OR generating multiple specific meals/recipes in one go (e.g., "find 2 recipes for Monday").
    - "FIND_RECIPE": Generating or finding a SINGLE recipe only.
    - "OTHER": Anything that doesn't fit the above.

    COMBINATIONS:
    - If the user asks to "plan my whole week" or "plan everything", output BOTH "WORKOUT" and "PLAN_MEAL" (and "SCHEDULE" if calendar integration is needed).
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
    """Validates the generated plans for conflicts and generates a friendly warning."""
    
    sys_prompt = """You are Nutrissistant, a helpful fitness and nutrition assistant. Compare the nutrition and fitness drafts. 
    Are there any critical conflicts? (e.g. heavy leg day with a zero-carb recovery meal, or a heavy meal scheduled 30 minutes before a high-intensity run).
    
    If there is a conflict, you MUST generate a friendly, conversational message for the user. Explain the issue simply, and ask if they would like you to fix it.
    
    Return JSON strictly matching this schema: 
    {
        "status": "ok" | "error", 
        "internal_reason": "Brief technical reason for the logs", 
        "friendly_message": "e.g., 'I noticed you have a heavy leg day planned on Tuesday, but your post-workout meal is very low in carbs. Would you like me to find a better recovery recipe?'"
    }"""
    
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
    
    # 🛑 CHECKPOINT 1: Save the user's chat message to disk IMMEDIATELY
    # This ensures it doesn't get lost if we reload the state later!
    state_manager.save_state(state)
    
    routine_generated = False
    latest_routine_response = None
    routine_draft = None
    recipe_data = None
    
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
            if key not in state:
                state[key] = []
            for item in extracted[key]:
                if item not in state[key]:
                    state[key].append(item)
                    context_changed = True
                    
    if context_changed:
        state_manager.save_state(state)
        shared_context["updated_user_context"] = {k: state.get(k, []) for k in context_keys}

    missing_info = intent_data.get("missing_info", [])
    
    raw_tasks = intent_data.get("tasks", [])
    task_aliases = {"PLAN_WORKOUT": "WORKOUT", "EXTRACT_WORKOUT": "WORKOUT"}
    def _extract_task_str(t):
        if isinstance(t, str):
            return t
        if isinstance(t, dict):
            return t.get("type") or t.get("task") or t.get("name") or ""
        return ""
    tasks = set(task_aliases.get(s, s) for s in (_extract_task_str(t) for t in raw_tasks) if s)

    # --- AUTONOMIC BEHAVIOR TRIGGER ---
    if "WORKOUT" in tasks or "PLAN_MEAL" in tasks or "FIND_RECIPE" in tasks:
        tasks.add("SCHEDULE")

    # Handle missing info first
    if missing_info:
        clarification = check_for_clarification(missing_info, step_tracer)
        state["status"] = "asking"
        state["missing_info"] = missing_info
        state_manager.save_state(state)
        return {"response": clarification, "steps": step_tracer}
    elif state.get("status") == "asking":
        state["status"] = "idle"
        state["missing_info"] = []
        state_manager.save_state(state)

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
        is_generation_planned = "WORKOUT" in tasks or "PLAN_MEAL" in tasks or "FIND_RECIPE" in tasks
        
        schedule_result = schedule_agent.execute_schedule_task(
            user_query=effective_query, 
            step_tracer=step_tracer, 
            shared_context=shared_context,
            mode="gather_constraints" if is_generation_planned else "execute_full"
        )
        shared_context = schedule_result.get("shared_context", shared_context)
        
        schedule_resp = schedule_result.get("response", "").strip()
        if schedule_resp:
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

    if "FIND_RECIPE" in tasks:
        recipe_context = {
            "request_type": "generate", 
            "meal_context": {
                 "free_time_mins": shared_context.get("meal_prep_time_limit_mins")
            },
            "constraints": {
                "dietary_restrictions": state.get("dietary_restrictions", []),
                "allergies": state.get("allergies", []),
                "excluded_ingredients": state.get("general_meal_restrictions", [])
            },
            "user_preferences": {
                "general": effective_query 
            }
        }
        
        recipe_result = run_recipe_extractor(recipe_context, step_tracer)
        
        if recipe_result.get("status") in ["success", "partial"]:
            recipe_data = recipe_result.get("recipe", {})
            recipe_name = recipe_data.get("name", "a matching recipe")
            prep_time = recipe_data.get("total_time_mins", "some")
            responses.append(f"I found a great recipe for you: {recipe_name}. It takes about {prep_time} minutes.")
        else:
            responses.append("I couldn't find a recipe that perfectly matched those constraints, but I can try generating a custom one if you'd like.")

    if "PLAN_MEAL" in tasks:
        meal_plan_context = {
            "dietary_restrictions": state.get("dietary_restrictions", []),
            "allergies": state.get("allergies", []),
            "excluded_ingredients": state.get("general_meal_restrictions", []),
            "query": effective_query
        }
        
        # Execute the agent
        meal_plan_result = meal_planner_agent.execute_weekly_meal_plan(
            context=meal_plan_context, 
            step_tracer=step_tracer
        )
        
        # Extract the schema you defined
        nutrition_draft = meal_plan_result.get("meal_plan_draft", {})
        latest_meal_response = meal_plan_result.get("response", "I've planned your meals based on your request.")
        responses.append(latest_meal_response)

    if "OTHER" in tasks:
        responses.append("I'm sorry, I can't help with that.")

    # Grab the active drafts (either newly generated, or loaded from existing state)
    active_fitness = routine_draft or fitness_draft 
    active_nutrition = nutrition_draft or state.get("plan_drafts", {}).get("nutrition")

    # If both exist in some form, run the passive warning check
    if active_fitness and active_nutrition:
        conflict_check = validate_and_resolve_conflicts(active_nutrition, active_fitness, step_tracer)
        if conflict_check.get("status") == "error":
            # Show the friendly conversational warning to the user
            responses.append(f"**Heads up:** {conflict_check.get('friendly_message')}")


    # ==========================================
    # PHASE 3: COMMIT & SYNC (Write)
    # ==========================================
    if routine_generated and isinstance(routine_draft, dict):
        routine_units = routine_draft.get("units", []) if isinstance(routine_draft.get("units"), list) else []
        
        # Maps the free slots gathered in Phase 1 and REMOVES them from the pool
        units_with_slots = _attach_slots_to_units(routine_units, shared_context.get("scheduled_slots", []))
        
        # --- NEW: WORKOUT FALLBACK SCHEDULER ---
        # If the LLM didn't give us enough slots for a full week, auto-fill them safely!
        fallback_days = ["Monday", "Wednesday", "Friday", "Tuesday", "Thursday", "Saturday", "Sunday"]
        used_days = [slot.get("day") for u in units_with_slots for slot in u.get("scheduled_slots", []) if slot.get("day")]
        
        for unit in units_with_slots:
            if not unit.get("scheduled_slots"):
                # Find a logical day not already used by this routine
                available_days = [d for d in fallback_days if d not in used_days]
                chosen_day = available_days[0] if available_days else "Monday"
                
                # Auto-fill to 17:00
                unit["scheduled_slots"] = [{"day": chosen_day, "time": "17:00"}]
                used_days.append(chosen_day)

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
        
        if "SCHEDULE" in tasks:
            schedule_agent.commit_routine_to_calendar(units_with_slots, step_tracer)
            responses.append("I have successfully added your workouts to the calendar.")
            
    # 🛑 CHECKPOINT 2: Refresh state from disk AFTER agents have done their saving!
    state = state_manager.load_state()

    # --- PROCESS MULTI-MEAL PLANS ---
    if "PLAN_MEAL" in tasks and nutrition_draft and isinstance(nutrition_draft.get("meals"), list):
        from uuid import uuid4
        
        # Safely initialize meals state if it doesn't exist yet
        if "meals" not in state:
            state["meals"] = {"plans": [], "current_plan_id": None}
            
        plan_id = f"plan_{uuid4().hex[:12]}"
        
        # Build the historical plan record
        new_plan = {
            "id": plan_id,
            "plan_name": "Weekly Meal Plan" if "week" in effective_query.lower() else "Custom Meal Plan",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "meals": nutrition_draft.get("meals", [])
        }
        
        state["meals"]["plans"].append(new_plan)
        state["meals"]["current_plan_id"] = plan_id

        # Loop through the array and schedule each meal independently
        if "SCHEDULE" in tasks:
            # Grab the list of slots your schedule agent gathered
            slots_pool = shared_context.get("scheduled_slots", []) 
            
            for meal in new_plan["meals"]:
                day = meal.get("day_of_week")
                meal_type = meal.get("meal_type", "lunch").lower()
                dishes = meal.get("dishes", [])
                
                if day and dishes:
                    dish_names = ", ".join([d.get("name", "Recipe") for d in dishes])
                    
                    # 1. Try to pull the exact slot the schedule agent found
                    if slots_pool:
                        target_slot = slots_pool.pop(0) 
                        day = target_slot.get("day") or day # Use agent's day, fallback to schema day
                        time = target_slot.get("time")
                    else:
                        # 2. Fallback if the schedule agent didn't find enough slots
                        time = "13:00" 
                        if meal_type == "breakfast": time = "08:00"
                        elif meal_type == "dinner": time = "19:00"
                        elif meal_type == "snack": time = "16:00"
                    
                    # Write to calendar
                    if "schedule_data" not in state:
                        state["schedule_data"] = {}
                    if day not in state["schedule_data"]:
                        state["schedule_data"][day] = {}
                        
                    state["schedule_data"][day][time] = f"Meal: {dish_names}"
            
            responses.append("I've successfully added these meals to your schedule.")

    if "FIND_RECIPE" in tasks and "SCHEDULE" in tasks and recipe_data:
        recipe_name = recipe_data.get("name", "New Recipe")
        slots = shared_context.get("scheduled_slots", [])
        
        # 1. Grab the slot the Schedule Agent found, or use a Fallback
        if slots:
            target_slot = slots[0] 
            day = target_slot.get("day")
            time = target_slot.get("time")
        else:
            # FALLBACK: If you didn't give a time and the schedule agent skipped it,
            # we assign it to a default dinner slot so it still shows up on the calendar.
            day = "Monday" 
            time = "19:00"
            
        if day and time:
            if "schedule_data" not in state:
                state["schedule_data"] = {}
            if day not in state["schedule_data"]:
                state["schedule_data"][day] = {}
                
            state["schedule_data"][day][time] = f"Meal: {recipe_name}"
            
            # If we used the fallback, explicitly tell the user where we put it
            if not slots:
                responses.append(f"I've added {recipe_name} to your schedule for {day} at {time}.")

    if "plan_drafts" not in state:
        state["plan_drafts"] = {"nutrition": [], "fitness": {}}
        
    if nutrition_draft is not None:
        state["plan_drafts"]["nutrition"] = nutrition_draft
    if fitness_draft is not None:
        state["plan_drafts"]["fitness"] = fitness_draft
        
    if recipe_data:
        # ---> FIX: Restore this line so the 'Current Recipes' UI can see it! <---
        state["last_found_recipe"] = recipe_data
        
        if isinstance(state["plan_drafts"].get("nutrition"), list):
            state["plan_drafts"]["nutrition"].append(recipe_data)

    state["user_query"] = effective_query
    state["status"] = "idle"

    final_response = "\n\n".join(filter(None, responses))

    if not final_response:
        final_response = "I'm sorry, I couldn't find an action to take based on that. Could you rephrase what you'd like to do?"

    # Safely append the agent's final answer to the freshly loaded history
    state["chat_history"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "agent",
        "content": final_response
    })
    
    # 🛑 CHECKPOINT 3: Final combined save
    state_manager.save_state(state)

    return {"response": final_response, "steps": step_tracer}