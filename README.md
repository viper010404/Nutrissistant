# Nutrissistant

Nutrissistant is a multi-agent wellness assistant that turns natural-language requests into actionable workout, scheduling, and lifestyle planning outputs.

## Project Overview

Nutrissistant is designed to feel like a personal wellness coordinator that can understand goals, remember user context, and turn broad requests into concrete plans.

In practice, users can write requests in natural language, for example:
- "Build me a weekly routine to lose weight."
- "Move my workout to another day."
- "Help me keep this plan realistic with my schedule."

The system then handles the planning process end to end:
1. Interprets what the user is trying to achieve.
2. Uses known profile and schedule context.
3. Produces an actionable response.
4. Returns transparent execution steps so decisions are inspectable.

Project goal:
- Provide a practical AI assistant for day-to-day wellness planning, not just a single chatbot answer.
- Combine planning, scheduling, and traceability in one user workflow.

## Architecture

### Architecture Diagram

![Nutrissistant Architecture](agent_architecture.png)


### Agent Components

**Supervisor Agent** — LLM Router and Orchestrator

The Supervisor acts as the reasoning backbone of the system. It receives every user prompt, classifies intent into task types (WORKOUT, SCHEDULE, PLAN_MEAL, FIND_RECIPE), extracts persistent context such as equipment, injuries, and dietary restrictions, and decides which agents to invoke and in which order. It runs a three-phase pipeline: gather constraints, execute domain tasks, commit outputs. At the end, it merges all agent results into a single coherent response and updates shared state.

**Schedule Agent** — ReAct-style Calendar Agent

The Schedule Agent operates in a ReAct pattern: it reasons about the current calendar state and takes targeted actions such as adding, removing, or rescheduling events. Given a user request, it parses the intended scheduling action, reads the live weekly schedule, applies a slot-finding algorithm, and writes the resolved event back to state. It can also run in a non-destructive constraint-gathering mode to provide available time slots to other agents before any plan is finalized.

**Workout Agent** — Reflection RAG Agent

The Workout Agent uses a Retrieval-Augmented Generation pipeline with an optional reflection loop. For new routine creation it runs iteratively: generate a candidate routine, then pass it to a critique LLM that checks safety, structure, and user alignment, then refine based on feedback. This repeats up to a configurable iteration limit. For smaller updates (swapping exercises, adjusting intensity) it falls back to a direct single-pass RAG generation. Scientific workout context is retrieved from a Pinecone vector index to ground generated plans in evidence.

**Recipe Extractor Agent** — Tool-Calling Agent

The Recipe Extractor is a LangChain `AgentExecutor`-based agent with a structured tool set. It reasons over a defined workflow: identify the target recipe component, choose a retrieval strategy (structured DB query, free-text DB query, or vector similarity search), build a candidate pool, and evaluate results through a two-stage pipeline: a hard-filter `STRICT_EVALUATOR` followed by a soft-ranking `LLM_EVALUATOR`. The final selected recipe is returned in a schema-validated output format. Fallback to LLM generation is available when retrieval returns no suitable candidates.

**Meal Planner Agent** — Structured Output Agent

The Meal Planner Agent operates against a defined input/output schema contract. It accepts meal context (meal type, day, available time, nutritional targets, restrictions) and returns a structured meal plan payload. It uses the Recipe Extractor Agent as a sub-component for individual dish resolution and is responsible for assembling complete daily or weekly nutrition plans within the constraints defined by the user profile.

### Supporting Runtime Components

`api.py`
- Exposes required endpoints and standard response envelopes.
- Serves architecture image and proxies non-API traffic to Streamlit.

`state_manager.py` + `user_data.json`
- Persistent state backbone shared by supervisor and task agents.

`main.py`
- Streamlit UI for prompt submission, response display, and step trace inspection.

### Runtime Sequence

1. Client sends prompt to `POST /api/execute`.
2. API forwards prompt to supervisor orchestration.
3. Supervisor classifies tasks and extracts persistent context updates.
4. If scheduling context is needed, the Scheduling Agent runs to gather slot constraints.
5. Domain agents execute (Workout Agent, Recipe Extractor Agent, Meal Planner contract flow as applicable).
6. Supervisor commits outputs and state updates (including schedule commit when relevant).
7. API returns:
   - `response`: final natural-language output.
   - `steps`: ordered module trace for observability.

### Data Contracts Between Components

- Execute request contract:
  - Input: `{ "prompt": "..." }`
  - Output envelope: `{ "status", "error", "response", "steps" }`
- Shared context contract:
  - Contains runtime constraints (for example discovered slots and duration limits).
- Routine draft contract:
  - Structured weekly units returned by workout generation and consumed by schedule commit logic.
- Persistent state contract:
  - Single source of truth in `user_data.json` accessed through `state_manager.py`.

## API Endpoints

Base URL for local run: `http://127.0.0.1:10000`

### 1) GET /health

Purpose: service liveness check.

Example response:

```json
{
  "status": "ok"
}
```

### 2) GET /api/team_info

Purpose: returns team metadata.

Response shape:

```json
{
  "group_batch_order_number": "{batch}_{order}",
  "team_name": "Nutrissistant",
  "students": [
    { "name": "...", "email": "..." }
  ]
}
```

### 3) GET /api/agent_info

Purpose: returns model description, purpose, and usage template/examples.

Response includes:
- `description`
- `purpose`
- `prompt_template`
- `prompt_examples`

### 4) GET /api/model_architecture

Purpose: returns architecture image.

Response:
- Content-Type: `image/png`
- Body: architecture PNG

### 5) POST /api/execute

Purpose: main agent entrypoint.

Request body:

```json
{
  "prompt": "User request here"
}
```

Success response:

```json
{
  "status": "ok",
  "error": null,
  "response": "...",
  "steps": []
}
```

Error response:

```json
{
  "status": "error",
  "error": "Human-readable error",
  "response": null,
  "steps": []
}
```

## User Instructions

### Prerequisites

- Python 3.11 (project runtime target: 3.11.9)
- Populated `.env` file (copy from `.env.example`)

### Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill values in `.env` for your environment.

### Run

```bash
bash start.sh
```

What this starts:
- Streamlit UI on `127.0.0.1:8501` (internal)
- FastAPI on `0.0.0.0:${PORT:-10000}`

### Basic API Checks

```bash
curl http://127.0.0.1:10000/health
curl http://127.0.0.1:10000/api/team_info
curl http://127.0.0.1:10000/api/agent_info
curl -o architecture.png http://127.0.0.1:10000/api/model_architecture
curl -X POST http://127.0.0.1:10000/api/execute \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Create a weekly wellness plan"}'
```

### UI Usage

1. Open the Streamlit app in browser.
2. Enter your profile/goals in the welcome flow.
3. Use the prompt box and click Run Agent.
4. Review final response and execution trace.
5. Inspect schedule and generated plan artifacts in the UI pages.

## Notes

- Keep secrets only in `.env` or deployment environment variables.
- For deployment, the same start command (`bash start.sh`) is used with platform-provided `PORT`.
- Team metadata and sample prompts in API info can be updated before final submission.
