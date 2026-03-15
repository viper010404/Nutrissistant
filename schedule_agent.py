import os
import json
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

import state_manager

# Load environment variables
load_dotenv()

LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
MODEL_NAME = "RPRTHPB-gpt-5-mini" 
MODULE_NAME = "ScheduleAgent" 

# JSON LLM for strict data extraction 
json_llm = ChatOpenAI(
    api_key=LLMOD_API_KEY,
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME,
).bind(response_format={"type": "json_object"})

def extract_schedule_intent(user_query, step_tracer):
    """
    Single LLM call to extract an ARRAY of scheduling parameters.
    """
    # --- NEW: Load schedule to show the LLM what's currently booked ---
    state = state_manager.load_state()
    schedule = state.get("schedule_data", {})
    
    booked_slots = []
    for d, times in schedule.items():
        for t, e in times.items():
            if e != "-":
                booked_slots.append(f"{d} at {t}: '{e}'")
    
    schedule_context = "\n".join(booked_slots) if booked_slots else "Schedule is currently empty."

    sys_prompt = f"""You are the Schedule Agent. Analyze the user query and extract all scheduling constraints.
    Assume 1 hour = 1 slot. 

    --- CURRENT BOOKED EVENTS ---
    {schedule_context}
    -----------------------------

    Actions:
    - ADD_HARD: Immovable events (meetings, doctor).
    - FIND_SLOT: Flexible events needing placement.
    - RESCHEDULE: Moving an existing event to a new time.
    - REMOVE: Deleting an existing event.
    - CHECK: User asking if they are free.

    CRITICAL RULES:
    1. LOGICAL TIME INFERENCE: If the user provides context (e.g., "morning before 9am"), calculate and output a specific valid time (e.g., "07:00"). If they say "Coffee", infer a logical daytime hour. Do NOT schedule non-logical times.
    2. RELATIVE DAYS: If the user asks to move an event to "another day" without specifying the day, output "OTHER" for the `preferred_day`.
    3. MATCHING: For RESCHEDULE or REMOVE, you MUST exactly extract ONLY the name inside the single quotes from the 'CURRENT BOOKED EVENTS' list for the `event_name` field. (e.g., If the list says `Monday at 18:00: 'Gym Workout'`, output `Gym Workout`, NOT the day/time).
    4. ROUTINE GENERATION: If the user asks to plan a routine (e.g., "plan 3 evening workouts"), output a "FIND_SLOT" action for EACH session. You MUST assign specific, spaced-out days (e.g., "Monday", "Wednesday", "Friday") for `preferred_day` to ensure they aren't scheduled on the same day.
    5. BIOLOGICAL SPACING (MEALS & WORKOUTS): You MUST analyze the CURRENT BOOKED EVENTS to prevent conflicts.
       - If scheduling a Workout: Do not place it within 2 hours after a scheduled meal.
       - If scheduling a Meal: If there is a workout that day, explicitly set `preferred_time` to 1-2 hours AFTER the workout for recovery, or 2+ hours BEFORE. 
       - Always output a specific `preferred_time` that respects these biological rules whenever possible.
       
    Output strictly in JSON with a list of "events":
    {{
        "events": [
            {{
                "action": "ADD_HARD" | "FIND_SLOT" | "RESCHEDULE" | "REMOVE" | "CHECK",
                "event_name": "string (MUST exactly match current schedule if RESCHEDULE/REMOVE)",
                "duration_slots": 1, 
                "preferred_day": "Monday" | "OTHER" | null, 
                "preferred_time": "18:00" 
            }}
        ]
    }}"""
    
    user_prompt = f"Query: {user_query}"

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]

    response = json_llm.invoke(messages)
    result = json.loads(response.content)
    
    # Required step object schema logging 
    step_tracer.append({
        "module": MODULE_NAME,
        "prompt": {"system": sys_prompt, "user": user_prompt},
        "response": result
    })
    
    return result

def find_available_slot(schedule, day, duration_slots):
    if day not in schedule: return None
    day_schedule = schedule[day]
    times = list(day_schedule.keys())
    for i in range(len(times) - duration_slots + 1):
        if all(day_schedule[times[i+j]] == "-" for j in range(duration_slots)):
            return times[i] 
    return None

def find_closest_available_slot(schedule, day, duration_slots, target_time, exclude_time=None):
    if day not in schedule: return None
    day_schedule = schedule[day]
    times = list(day_schedule.keys())
    target_hour = int(target_time.split(":")[0])
    
    valid_starts = []
    for i in range(len(times) - duration_slots + 1):
        if all(day_schedule[times[i+j]] == "-" for j in range(duration_slots)):
            start_time = times[i]
            if start_time == exclude_time:
                continue
            start_hour = int(start_time.split(":")[0])
            distance = abs(start_hour - target_hour)
            valid_starts.append((distance, start_time))
            
    if valid_starts:
        valid_starts.sort(key=lambda x: x[0])
        return valid_starts[0][1] 
    return None

def remove_event_from_schedule(schedule, event_name):
    removed = False
    orig_day = None
    orig_time = None
    event_lower = event_name.lower()
    for d in schedule:
        for t in schedule[d]:
            if schedule[d][t] != "-" and event_lower in schedule[d][t].lower():
                if not removed: 
                    orig_day = d
                    orig_time = t
                schedule[d][t] = "-"
                removed = True
    return removed, orig_day, orig_time

import copy # Make sure to import this at the top of schedule_agent.py

def execute_schedule_task(user_query, step_tracer, shared_context=None, mode="execute_full"):
    if shared_context is None:
        shared_context = {"scheduled_slots": []}
        
    state = state_manager.load_state()
    
    # NEW: Create a sandbox schedule. This prevents double-booking proposed slots 
    # without mutating the real state too early.
    sandbox_schedule = copy.deepcopy(state.get("schedule_data", {}))
    
    intent_data = extract_schedule_intent(user_query, step_tracer)
    events = intent_data.get("events", [])
    
    agent_messages = []
    ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for event in events:
        action = event.get("action")
        event_name = event.get("event_name", "Event")
        duration = event.get("duration_slots", 1)
        pref_day = event.get("preferred_day") 
        pref_time = event.get("preferred_time")
        
        anchor_day = pref_day
        anchor_time = pref_time
        
        if action == "ADD_HARD":
            if pref_day and pref_day in sandbox_schedule and pref_time in sandbox_schedule[pref_day]:
                sandbox_schedule[pref_day][pref_time] = f"{event_name}"
                agent_messages.append(f"Scheduled hard event '{event_name}' on {pref_day} at {pref_time}.")
            else:
                agent_messages.append(f"Could not find valid time slot for '{event_name}'.")
                
        elif action == "REMOVE":
            was_removed, _, _ = remove_event_from_schedule(sandbox_schedule, event_name)
            if was_removed:
                agent_messages.append(f"Removed '{event_name}' from your schedule.")
            else:
                agent_messages.append(f"Could not find '{event_name}' to remove.")
                
        elif action in ["FIND_SLOT", "RESCHEDULE"]:
            orig_day = None
            orig_time = None
            
            if action == "RESCHEDULE":
                was_removed, orig_day, orig_time = remove_event_from_schedule(sandbox_schedule, event_name)
                if not was_removed:
                    agent_messages.append(f"Could not find '{event_name}' to reschedule.")
                    continue
                agent_messages.append(f"Cleared old '{event_name}' to make room for reschedule.")
                
                if pref_day == "OTHER":
                    anchor_day = None 
                elif not anchor_day: 
                    anchor_day = orig_day
                    
                if not anchor_time: 
                    anchor_time = orig_time
            
            found_time = None
            found_day = None
            
            if pref_day == "OTHER" and orig_day:
                start_idx = (ALL_DAYS.index(orig_day) + 1) % 7
                days_to_search = ALL_DAYS[start_idx:] + ALL_DAYS[:start_idx]
            elif anchor_day and anchor_day in ALL_DAYS:
                start_idx = ALL_DAYS.index(anchor_day)
                days_to_search = ALL_DAYS[start_idx:] + ALL_DAYS[:start_idx]
            else:
                days_to_search = ALL_DAYS

            if anchor_day and anchor_time and sandbox_schedule.get(anchor_day, {}).get(anchor_time) == "-":
                if action == "RESCHEDULE" and anchor_day == orig_day and anchor_time == orig_time and not pref_time:
                    pass 
                else:
                    found_day = anchor_day
                    found_time = anchor_time
            
            if not found_time:
                for search_day in days_to_search:
                    time_to_block = orig_time if (action == "RESCHEDULE" and search_day == orig_day and not pref_time) else None
                    
                    if anchor_time:
                        found_time = find_closest_available_slot(sandbox_schedule, search_day, duration, anchor_time, exclude_time=time_to_block)
                    else:
                        found_time = find_available_slot(sandbox_schedule, search_day, duration)
                        
                    if found_time:
                        found_day = search_day
                        break
            
            if found_day and found_time:
                times = list(sandbox_schedule[found_day].keys())
                start_idx = times.index(found_time)
                
                # NEW: Only use the "Hold:" prefix if we are in gathering mode
                for j in range(duration):
                    event_val = f"Hold: {event_name}" if mode == "gather_constraints" else event_name
                    sandbox_schedule[found_day][times[start_idx + j]] = event_val
                
                if mode == "gather_constraints":
                    verb = "Proposed"
                else:
                    verb = "Rescheduled" if action == "RESCHEDULE" else "Placed"
                    
                agent_messages.append(f"{verb} '{event_name}' on {found_day} at {found_time}.")
                shared_context["scheduled_slots"].append({"day": found_day, "time": found_time, "event": event_name})
                
                duration_mins = duration * 60
                event_lower = event_name.lower()
                if "workout" in event_lower or "exercise" in event_lower or "gym" in event_lower:
                    shared_context["workout_time_limit_mins"] = duration_mins
                elif "meal" in event_lower or "cook" in event_lower:
                    shared_context["meal_prep_time_limit_mins"] = duration_mins
                else:
                    shared_context["other_event_limit_mins"] = duration_mins
            else:
                agent_messages.append(f"No available slots found for '{event_name}'.")

        elif action == "CHECK":
            if pref_day and pref_time and sandbox_schedule.get(pref_day, {}).get(pref_time) == "-":
                agent_messages.append(f"Yes, you are free on {pref_day} at {pref_time}.")
            else:
                current_event = sandbox_schedule.get(pref_day, {}).get(pref_time, "Unknown")
                agent_messages.append(f"No, you have '{current_event}' scheduled on {pref_day} at {pref_time}.")

    if mode == "gather_constraints":
        for d in sandbox_schedule:
            for t in sandbox_schedule[d]:
                if sandbox_schedule[d][t].startswith("Hold: "):
                    sandbox_schedule[d][t] = "-"

    # save the state
    state["schedule_data"] = sandbox_schedule
    state_manager.save_state(state)

    final_response = " ".join(agent_messages) if agent_messages else "Checked schedule constraints."

    return {
        "response": final_response,
        "shared_context": shared_context
    }


def commit_routine_to_calendar(units_with_slots, step_tracer):
    """
    Takes a finalized routine from another agent (Workout, Meal Planner) 
    and locks it into the calendar.
    """
    state = state_manager.load_state()
    schedule = state.get("schedule_data", {})
    messages = []
    
    for unit in units_with_slots:
        # Fallback naming to support future Meal Planner agents as well
        event_name = unit.get("title") or unit.get("meal_name") or unit.get("name") or "Scheduled Activity"
        
        for slot in unit.get("scheduled_slots", []):
            day = slot.get("day")
            time = slot.get("time")
            
            if day and time and day in schedule and time in schedule[day]:
                schedule[day][time] = event_name
                messages.append(f"Successfully scheduled '{event_name}' on {day} at {time}.")
                
    state["schedule_data"] = schedule
    state_manager.save_state(state)
    
    # Log the commit action
    step_tracer.append({
        "module": MODULE_NAME,
        "stage": "commit_to_calendar",
        "data": units_with_slots,
        "response": messages
    })
    
    return {"status": "success", "messages": messages}