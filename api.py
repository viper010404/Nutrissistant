from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os

import supervisor_agent

app = FastAPI(title="Nutrissistant API")

# --- DATA MODELS ---
# Matches the input format required by the PDF: { "prompt": "User request here" }
class ExecuteRequest(BaseModel):
    prompt: str

# --- API ENDPOINTS ---

@app.get("/api/team_info")
def get_team_info():
    """Returns student details as required."""
    # Ensure you update this with your actual team details
    return {
        "group_batch_order_number": "01_01", 
        "team_name": "Nutrissistant",
        "students": [
            { "name": "Galit Kadzelashvily", "email": "galit.k@campus.technion.ac.il" },
            { "name": "Shachar Frenkel", "email": "fshachar@campus.technion.ac.il" },
            { "name": "Ido Falach", "email": "ido.falah@campus.technion.ac.il" }
        ]
    }

@app.get("/api/agent_info")
def get_agent_info():
    """Returns agent meta and how to use it."""
    return {
        "description": "Nutrissistant is an autonomous wellness and healthy lifestyle agent.",
        "purpose": "To create personalized nutrition and fitness plans while resolving scheduling conflicts.",
        "prompt_template": {
            "template": "I am [Age/Gender]. My goal is to [Goal]. I have access to [Equipment/Location]. Please plan my week."
        },
        "prompt_examples": [
            {
                "prompt": "I want to lose 3kg and I only have dumbbells at home.",
                "full_response": "I have successfully created your plans and updated your schedule!",
                "steps": [
                    {
                        "module": "Supervisor",
                        "prompt": {"system": "...", "user": "..."},
                        "response": {"intent": "PLANNING"}
                    }
                ]
            }
        ]
    }

@app.get("/api/model_architecture")
def get_model_architecture():
    """Returns the architecture diagram as a PNG image."""
    image_path = "architecture_diagram.png" # Make sure this file exists in your folder!
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="Architecture diagram not found.")
    
    return FileResponse(image_path, media_type="image/png")

@app.post("/api/execute")
def execute_agent(request: ExecuteRequest):
    """The main entry point. User sends prompt, API returns response and steps trace."""
    try:
        # Call the logic we built earlier
        agent_result = supervisor_agent.orchestrate_workflow(request.prompt)
        
        # Format the response exactly as the PDF requires
        return {
            "status": "ok",
            "error": None,
            "response": agent_result["response"],
            "steps": agent_result["steps"]
        }
    except Exception as e:
        # If anything crashes, return the required error schema
        return {
            "status": "error",
            "error": str(e),
            "response": None,
            "steps": []
        }