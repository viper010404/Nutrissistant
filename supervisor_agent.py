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

def analyze_intent_and_extract_metadata(user_query, user_profile, step_tracer):
    """Analyzes intent and extracts missing info."""
    
    sys_prompt = """You are the Nutrissistant Supervisor. Analyze the user query against their profile.
    
CRITICAL RULES FOR 'missing_info' (Avoid unnecessary ping-pong):
1. MINIMIZE QUESTIONS: Only ask for absolute blockers (e.g., 'home' vs 'gym').
2. ASSUME GYM EQUIPMENT: If the user mentions a 'gym' for example, assume access to standard equipment (dumbbells, barbells, machines, cardio). DO NOT ask for an equipment list.
3. DO NOT ASK FOR SCHEDULE: The system has a dedicated Schedule Agent that will find the appropriate time and duration for the workout based on the user's calendar. Make sure that the workout is not too long or too short based on user'd profile and goals.
4. ASSUME DEFAULTS: Assume an intermediate fitness level and no injuries unless the user explicitly mentions them. Do not ask for medical history.

Output JSON only with this schema:
{
    "intent": "PLANNING" or "GENERAL_QUESTION",
    "goals": ["extracted goal 1"],
    "missing_info": [] // Array of strings. Keep empty [] if the rules above apply.
}"""
    user_prompt = f"Profile: {user_profile}\nQuery: {user_query}"

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]

    # Call the JSON-bound LangChain model
    response = json_llm.invoke(messages)
    result = json.loads(response.content)
    
    # Log the step 
    step_tracer.append({
        "module": MODULE_NAME,
        "prompt": {"system": sys_prompt, "user": user_prompt},
        "response": result
    })
    
    return result

def generate_tasks_from_intent(intent_data):
    """
    generates a list of tasks for the sub-agents based on the extracted intent and missing info.
    """
    tasks = []
    missing_info = intent_data.get("missing_info", [])

    if missing_info:
        tasks = ["ask_clarification"]
    elif intent_data.get("intent") == "GENERAL_QUESTION":
        tasks = ["answer_general"]
    elif intent_data.get("intent") == "PLANNING":
        tasks = ["check_schedule", "plan_nutrition", "plan_fitness"]
        
    return tasks, missing_info

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

def orchestrate_workflow(user_query):
    """Main orchestration loop."""
    
    state = get_user_data()
    step_tracer = [] 
    final_response = ""

    # Extract Intent
    intent_data = analyze_intent_and_extract_metadata(user_query, state["user_profile"], step_tracer)
    
    # Generate Task List
    tasks, missing_info = generate_tasks_from_intent(intent_data)

    # Execute Tasks based on State
    if "ask_clarification" in tasks:
        state["status"] = "asking"
        final_response = check_for_clarification(missing_info, step_tracer)
        state["missing_info"] = missing_info
        state_manager.save_state(state)
        
        return {"response": final_response, "steps": step_tracer}

    if "answer_general" in tasks:
        final_response = "Placeholder: Routed to expert for general answer."
        return {"response": final_response, "steps": step_tracer}

    if "check_schedule" in tasks:
        state["status"] = "planning"
        
        # Placeholder for sub-agent execution
        nutrition_draft = "Placeholder Nutrition Plan"
        fitness_draft = "Placeholder Fitness Plan"
        
        validation = validate_and_resolve_conflicts(nutrition_draft, fitness_draft, step_tracer)
        
        if validation["status"] == "ok":
            state["plan_drafts"]["nutrition"] = nutrition_draft
            state["plan_drafts"]["fitness"] = fitness_draft
            state["status"] = "idle"
            final_response = "I have successfully created your plans and updated your schedule!"
        else:
            final_response = f"I encountered an issue while planning: {validation['reason']}. Let me adjust."
            
        state_manager.save_state(state)

    return {"response": final_response, "steps": step_tracer}