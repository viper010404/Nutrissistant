
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os
import json

from src.config import LLM_MODEL_NAME

load_dotenv()

LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
MODEL_NAME = LLM_MODEL_NAME
DEFAULT_MODULE_NAME = "LLM"


text_llm = ChatOpenAI(
    api_key=LLMOD_API_KEY,
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME,
)


json_llm = ChatOpenAI(
    api_key=LLMOD_API_KEY,
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME,
).bind(response_format={"type": "json_object"})


def _append_step(step_tracer, system_prompt, user_prompt, response, module_name: str = DEFAULT_MODULE_NAME):
    if step_tracer is None:
        return

    step_payload = {
        "module": module_name,
        "prompt": {"system": system_prompt, "user": user_prompt},
        "response": response,
    }

    if hasattr(step_tracer, "append"):
        step_tracer.append(step_payload)
    elif hasattr(step_tracer, "log"):
        step_tracer.log(module_name, step_payload)


def _coerce_user_prompt(payload):
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_json_response_content(content):
    if isinstance(content, list):
        content = "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
        )
    return json.loads(content)


def _invoke_json_llm(system_prompt, user_payload, step_tracer=None, module_name: str = DEFAULT_MODULE_NAME):
    user_prompt = _coerce_user_prompt(user_payload)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    response = json_llm.invoke(messages)
    result = _parse_json_response_content(response.content)
    _append_step(step_tracer, system_prompt, user_prompt, result, module_name)
    return result
