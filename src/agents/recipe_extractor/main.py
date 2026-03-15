"""
Recipe Extractor Agent – entry point.

Usage
-----
    from src.agents.recipe_extractor.main import run_recipe_extractor
    result = run_recipe_extractor(context_dict)

Or run directly:
    python -m src.agents.recipe_extractor.main
"""

from __future__ import annotations

import json
import os
import sys
from importlib import import_module
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from src.config import LLM_MODEL_NAME
from src.utils.LLM_utils import LLMOD_API_KEY, OPENAI_API_BASE

try:
    from langchain.agents import AgentExecutor, create_openai_tools_agent
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    HAS_LEGACY_AGENT_API = True
except ImportError:
    from langchain.agents import create_agent

    AgentExecutor = Any
    HAS_LEGACY_AGENT_API = False

load_dotenv()

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(CURRENT_DIR)
SRC_DIR = os.path.dirname(AGENTS_DIR)
PROJECT_ROOT = os.path.dirname(SRC_DIR)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

RECIPE_EXTRACTOR_MODULE_PREFIXES = (
    "src.agents.recipe_extractor",
)

# ── lazy module imports (avoids circular deps & heavy init at module level) ──
_prompts = None
_tools = None


def _import_recipe_module(module_suffix: str):
    last_error = None
    for prefix in RECIPE_EXTRACTOR_MODULE_PREFIXES:
        try:
            return import_module(f"{prefix}.{module_suffix}")
        except ModuleNotFoundError as exc:
            last_error = exc
    raise last_error


def _get_prompts():
    global _prompts
    if _prompts is None:
        _prompts = _import_recipe_module("prompts")
    return _prompts


def _get_tools_module():
    global _tools
    if _tools is None:
        _tools = _import_recipe_module("tools")
    return _tools


# ── LLM configuration ───────────────────────────────────────────────────────
MODEL_NAME = os.getenv("RECIPE_AGENT_MODEL", LLM_MODEL_NAME)


# ── agent builder ─────────────────────────────────────────────────────────────
def _build_all_tools(step_tracer=None, base_context=None) -> list:
    tm = _get_tools_module()
    return (
        tm.build_generation_tools(step_tracer, base_context)
        + tm.build_evaluation_tools(step_tracer, base_context)
        + tm.build_refinement_tools(step_tracer, base_context)
        + tm.build_output_tools()
    )


def _build_agent(step_tracer=None, base_context=None):
    """Build and return a fresh AgentExecutor for the recipe extractor."""
    prompts = _get_prompts()
    all_tools = _build_all_tools(step_tracer, base_context)

    llm = ChatOpenAI(
        api_key=LLMOD_API_KEY,
        base_url=OPENAI_API_BASE,
        model=MODEL_NAME,
    )

    if HAS_LEGACY_AGENT_API:
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", prompts.MAIN_RECIPE_EXTRACTOR_SYSTEM_PROMPT),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        agent = create_openai_tools_agent(llm, all_tools, prompt)
        return AgentExecutor(
            agent=agent,
            tools=all_tools,
            verbose=True,
            max_iterations=20,
            handle_parsing_errors=True,
        )

    return create_agent(
        model=llm,
        tools=all_tools,
        system_prompt=prompts.MAIN_RECIPE_EXTRACTOR_SYSTEM_PROMPT,
        debug=True,
        name="recipe_extractor_agent",
    )


def _extract_agent_output(raw: Any):
    if isinstance(raw, dict):
        if "output" in raw:
            return raw.get("output", "")

        messages = raw.get("messages")
        if isinstance(messages, list) and messages:
            last_message = messages[-1]
            if isinstance(last_message, dict):
                return last_message.get("content", "")
            return getattr(last_message, "content", "")

    return raw


def _copy_meal_id_from_context(parsed: dict, context: dict) -> dict:
    if not isinstance(parsed, dict) or not isinstance(context, dict):
        return parsed

    input_meal_context = context.get("meal_context")
    if not isinstance(input_meal_context, dict):
        return parsed

    meal_id = input_meal_context.get("meal_id")
    if meal_id is None:
        return parsed

    output_meal_context = parsed.get("meal_context")
    if not isinstance(output_meal_context, dict):
        output_meal_context = {}
        parsed["meal_context"] = output_meal_context

    output_meal_context["meal_id"] = meal_id
    return parsed


# Singleton – built once, reused across calls
_agent_executor = None


def _get_agent(step_tracer=None, base_context=None):
    global _agent_executor
    if step_tracer is not None:
        # Build a fresh agent per call so step_tracer is baked into each tool via partial
        return _build_agent(step_tracer, base_context)
    if _agent_executor is None:
        _agent_executor = _build_agent(None, None)
    return _agent_executor


# ── public API ────────────────────────────────────────────────────────────────
def run_recipe_extractor(context: dict, step_tracer: list | None = None) -> dict:
    """
    Run the Recipe Extractor Agent.

    Parameters
    ----------
    context : dict
        Must follow the expected recipe-extractor input shape.
    step_tracer : list, optional
        If provided, each LLM call's prompt + response is appended to this list
        for debugging / logging.

    Returns
    -------
    dict
        Agent output following the expected recipe-extractor output shape.
        Always contains at least: ``status``, ``source``, ``meal_context``,
        ``recipe``, ``warnings``, ``suggestions``.
        On hard failure: ``status="failed"`` with a ``message`` key.
    """
    if not isinstance(context, dict):
        return {
            "status": "failed",
            "source": "input_validation",
            "message": "context must be a dict.",
            "warnings": [],
            "suggestions": [],
        }

    agent = _get_agent(step_tracer, context)
    input_str = json.dumps(context, ensure_ascii=False, indent=2)

    try:
        if HAS_LEGACY_AGENT_API:
            raw = agent.invoke({"input": input_str})
        else:
            raw = agent.invoke({"messages": [{"role": "user", "content": input_str}]})

        output = _extract_agent_output(raw)

        if isinstance(output, str):
            output = output.strip()
            # Strip markdown code fences if the agent wrapped JSON in them
            if output.startswith("```"):
                lines = output.splitlines()
                output = "\n".join(
                    line for line in lines if not line.startswith("```")
                ).strip()
            try:
                parsed: dict = json.loads(output)
            except json.JSONDecodeError:
                return {
                    "status": "failed",
                    "source": "agent_output",
                    "message": "Agent returned non-JSON output.",
                    "raw_output": output,
                    "warnings": [],
                    "suggestions": [],
                }
        elif isinstance(output, dict):
            parsed = output
        else:
            return {
                "status": "failed",
                "source": "agent_output",
                "message": f"Unexpected output type: {type(output).__name__}",
                "warnings": [],
                "suggestions": [],
            }

        parsed = _copy_meal_id_from_context(parsed, context)

        return parsed

    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "source": "agent_execution",
            "message": f"Agent raised an exception: {exc}",
            "warnings": [],
            "suggestions": [],
        }

# Vector DB test context — vague/style-driven, no structured filters
# Expected agent path: STRACTURED_DATABASE_QUERY fails/returns empty → USE_VECTOR_DB
if __name__ == "__main__":
    import json
    context = {
        "request_type": "generate",
        "meal_context": {
            "meal_id": "meal_vector_test",
            "meal_type": "dinner",
            "day_of_week": "Wednesday",
            "free_time_mins": 60,
            "description": (
                "Something like a cozy Middle-Eastern-inspired lamb or chickpea stew "
                "with warming spices like cumin, coriander and cinnamon. "
                "Hearty and comforting, similar to a tagine or a slow-cooked "
                "North African dish. Not a specific recipe — just something in that vibe."
            ),
            "course": "main_course",
            "components": ["main_course"],
        },
        "nutritional_targets_min": {},
        "nutritional_targets_max": {},
        "constraints": {
            "dietary_restrictions": [],
            "allergies": ["peanuts"],
            "excluded_ingredients": ["pork", "alcohol"],
            "available_ingredients": [],
            "must_have_all_ingredients": False,
            "max_ingredients": 15,
        },
        "revision_context": {
            "original_recipe_id": None,
            "original_recipe_name": None,
            "feedback": "",
            "keep_base_recipe": False,
        },
        "user_preferences": {
            "cooking_skill": "intermediate",
            "prefer_simple_recipes": False,
            "spice_tolerance": "medium",
        },
    }

    steps = []
    result = run_recipe_extractor(context, step_tracer=steps)
    print(result.get("source"))
    print(json.dumps(result, indent=2, ensure_ascii=False))