import os
import json
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

import schedule_agent
import state_manager

# Load environment variables
load_dotenv()

LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
MODEL_NAME = "RPRTHPB-gpt-5-mini"
MODULE_NAME = "Supervisor" 

text_llm = ChatOpenAI(
    api_key=LLMOD_API_KEY,
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME
)

# JSON LLM for strict data extraction 
json_llm = ChatOpenAI(
    api_key=LLMOD_API_KEY,
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME,
).bind(response_format={"type": "json_object"})


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
    - "PLAN_WORKOUT": Planning fitness/workouts.
    - "FIND_RECIPE": Searching for or extracting recipe details.
    - "EXTRACT_WORKOUT": Extracting existing workout details or making workout modifications.
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
    result = json.loads(response.content)
    
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
    result = json.loads(response.content)
    
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
    "EXTRACT_WORKOUT": 2,
    "SCHEDULE": 3,      
    "PLAN_MEAL": 4,    
    "PLAN_WORKOUT": 4   
}

def orchestrate_workflow(user_query):
    """Main orchestration loop with dynamic constraint passing."""
    
    state = get_user_data()
    step_tracer = [] 
    responses = [] 
    
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

    # Handle Clarifications First
    if missing_info:
        state["status"] = "asking"
        clarification = check_for_clarification(missing_info, step_tracer)
        state["missing_info"] = missing_info
        state_manager.save_state(state)
        return {"response": clarification, "steps": step_tracer}

    # Sort tasks by the new priority
    tasks = sorted(tasks, key=lambda x: TASK_PRIORITY.get(x, 99))

    nutrition_draft = state.get("plan_drafts", {}).get("nutrition", None)
    fitness_draft = state.get("plan_drafts", {}).get("fitness", None)

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

        elif task == "PLAN_WORKOUT":
            # The Exercise Planner now reads from the shared context!
            time_limit = shared_context.get("workout_time_limit_mins", 60) # Default to 60 if no schedule was requested
            
            fitness_draft = f"Placeholder Fitness Plan (Strictly {time_limit} minutes)"
            responses.append(f"Placeholder: Planned your {time_limit}-minute workout.")

        elif task == "PLAN_MEAL":
            # Meal planner can also use constraints (e.g., if schedule only leaves 15 mins for cooking)
            prep_limit = shared_context.get("meal_prep_time_limit_mins", 30)
            
            nutrition_draft = f"Placeholder Nutrition Plan (Under {prep_limit} mins prep)"
            responses.append("Placeholder: Planned your meals.")
            
        elif task == "FIND_RECIPE":
            responses.append("Placeholder: Found your recipe.")
            
        elif task == "EXTRACT_WORKOUT":
            responses.append("Placeholder: Extracted workout details.")
            
        elif task == "GENERAL_QUESTION":
            responses.append("Placeholder: Answered general question.")
            
        elif task == "OTHER":
            responses.append("Placeholder: Handled 'other' request.")

    state = state_manager.load_state()
    # Save final drafts and update status
    if "plan_drafts" not in state:
        state["plan_drafts"] = {}
    
    if nutrition_draft: state["plan_drafts"]["nutrition"] = nutrition_draft
    if fitness_draft: state["plan_drafts"]["fitness"] = fitness_draft
    
    state["status"] = "idle"
    state_manager.save_state(state)

    final_response = "\n".join(responses)

    return {"response": final_response, "steps": step_tracer}