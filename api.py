from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import asyncio
import os

import httpx
import websockets

import supervisor_agent

app = FastAPI(title="Nutrissistant API")

# --- DATA MODELS ---
# Matches the input format required by the PDF: { "prompt": "User request here" }
class ExecuteRequest(BaseModel):
    prompt: str

# --- API ENDPOINTS ---

@app.get("/health")
def health_check():
    """Lightweight health endpoint for uptime checks."""
    return {"status": "ok"}

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


# ---------------------------------------------------------------------------
# Streamlit proxy — forward all non-API traffic to the local Streamlit process
# ---------------------------------------------------------------------------
_STREAMLIT_HTTP = "http://localhost:8501"
_STREAMLIT_WS = "ws://localhost:8501"
_HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "transfer-encoding", "te",
    "trailer", "proxy-authorization", "proxy-authenticate",
    "upgrade", "content-encoding",
])


@app.websocket("/{path:path}")
async def _ws_proxy(path: str, websocket: WebSocket):
    """Proxy WebSocket connections to the Streamlit process."""
    await websocket.accept()
    qs = websocket.scope.get("query_string", b"").decode()
    target = f"{_STREAMLIT_WS}/{path}" + (f"?{qs}" if qs else "")
    try:
        async with websockets.connect(target) as upstream:
            async def _to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if msg.get("bytes"):
                            await upstream.send(msg["bytes"])
                        elif msg.get("text"):
                            await upstream.send(msg["text"])
                except (WebSocketDisconnect, Exception):
                    pass

            async def _to_client():
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(str(message))
                except Exception:
                    pass

            await asyncio.gather(_to_upstream(), _to_client())
    except Exception:
        pass


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def _http_proxy(path: str, request: Request):
    """Proxy all remaining HTTP requests to the Streamlit process."""
    qs = request.scope.get("query_string", b"").decode()
    target = f"{_STREAMLIT_HTTP}/{path}" + (f"?{qs}" if qs else "")
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }
    body = await request.body()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.request(
            method=request.method, url=target,
            headers=fwd_headers, content=body,
        )
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)