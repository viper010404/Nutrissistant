import os
import json
import re
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_core.messages import SystemMessage, HumanMessage
from pinecone import Pinecone
from pydantic import SecretStr

# Load environment variables
load_dotenv()

LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME_WORKOUTS = os.getenv("PINECONE_INDEX_NAME_WORKOUTS")
MODEL_NAME = "RPRTHPB-gpt-5-mini"
EMBEDDING_MODEL = "RPRTHPB-text-embedding-3-small"
MODULE_NAME = "WorkoutAgent"

json_llm = ChatOpenAI(
    api_key=SecretStr(LLMOD_API_KEY or ""),
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME,
).bind(response_format={"type": "json_object"})


def _fetch_workout_rag_context(user_query, top_k=4, max_chunk_chars=1200):
    """Retrieves workout-relevant evidence chunks from Pinecone."""
    if not user_query:
        return {
            "status": "skipped",
            "reason": "empty_query",
            "context": "",
            "sources": []
        }

    if not (PINECONE_API_KEY and PINECONE_INDEX_NAME_WORKOUTS and LLMOD_API_KEY and OPENAI_API_BASE):
        return {
            "status": "disabled",
            "reason": "missing_env",
            "context": "",
            "sources": []
        }

    try:
        pc = Pinecone(api_key=PINECONE_API_KEY)
        index = pc.Index(PINECONE_INDEX_NAME_WORKOUTS)

        embeddings = OpenAIEmbeddings(
            api_key=SecretStr(LLMOD_API_KEY or ""),
            base_url=OPENAI_API_BASE,
            model=EMBEDDING_MODEL
        )
        query_vector = embeddings.embed_query(user_query)

        query_result = index.query(
            vector=query_vector,
            top_k=top_k,
            include_metadata=True
        )

        matches = []
        if isinstance(query_result, dict):
            matches = query_result["matches"] if "matches" in query_result else []
        else:
            matches = getattr(query_result, "matches", [])
        if not matches:
            return {
                "status": "ok",
                "reason": "no_matches",
                "context": "",
                "sources": []
            }

        sources = []
        context_blocks = []
        for i, match in enumerate(matches[:top_k], start=1):
            metadata = match.get("metadata", {}) if isinstance(match, dict) else getattr(match, "metadata", {})
            score = match.get("score") if isinstance(match, dict) else getattr(match, "score", None)
            pmcid = metadata.get("pmcid", "unknown")
            chunk_text = (metadata.get("chunk_text", "") or "")[:max_chunk_chars].strip()
            if not chunk_text:
                continue

            score_value = round(float(score), 4) if score is not None else None
            sources.append({
                "pmcid": pmcid,
                "score": score_value
            })

            source_header = f"Source {i} | PMCID: {pmcid}"
            if score_value is not None:
                source_header += f" | score: {score_value}"
            context_blocks.append(f"{source_header}\n{chunk_text}")

        return {
            "status": "ok",
            "reason": "retrieved",
            "context": "\n\n".join(context_blocks),
            "sources": sources
        }

    except Exception as e:
        return {
            "status": "error",
            "reason": str(e),
            "context": "",
            "sources": []
        }


def _infer_routine_operation(user_query):
    q = (user_query or "").lower()
    update_keywords = [
        "add",
        "incorporate",
        "update",
        "modify",
        "change",
        "replace",
        "swap",
        "remove",
        "adjust",
    ]
    if any(keyword in q for keyword in update_keywords):
        return "update"
    return "create"


def _extract_requested_unit_count(user_query):
    q = (user_query or "").lower()
    keyword_to_num = {
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
    }
    for word, num in keyword_to_num.items():
        if f"{word} workout" in q or f"{word} session" in q:
            return num

    digits = "".join(ch if ch.isdigit() else " " for ch in q).split()
    for token in digits:
        value = int(token)
        if 2 <= value <= 7:
            return value

    return None


def _sanitize_user_text(text):
    if not isinstance(text, str):
        return text

    cleaned = text
    # Remove direct backend/source references.
    cleaned = re.sub(r"\bPMCID\s*[:#]?\s*\w+\b", "research", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bSource\s*\d+\b", "research", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bscore\s*[:=]?\s*\d+(?:\.\d+)?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[[^\]]*PMCID[^\]]*\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _sanitize_user_payload(value):
    if isinstance(value, str):
        return _sanitize_user_text(value)
    if isinstance(value, list):
        return [_sanitize_user_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_user_payload(val) for key, val in value.items()}
    return value


def _build_schedule_guidance(shared_context):
    if not isinstance(shared_context, dict):
        return ""

    guidance_parts = []

    schedule_limit = shared_context.get("workout_time_limit_mins")
    if isinstance(schedule_limit, int) and schedule_limit > 0:
        guidance_parts.append(
            f"Calendar context suggests one available workout block is around {schedule_limit} minutes."
        )

    slots = shared_context.get("scheduled_slots")
    if isinstance(slots, list) and slots:
        slot_labels = []
        for slot in slots[:4]:
            if isinstance(slot, dict):
                day = slot.get("day", "")
                time = slot.get("time", "")
                label = f"{day} {time}".strip()
                if label:
                    slot_labels.append(label)
        if slot_labels:
            guidance_parts.append("Scheduled slots include: " + ", ".join(slot_labels) + ".")

    return " ".join(guidance_parts)


def _fallback_weekly_routine(user_query, requested_units, schedule_guidance=""):
    unit_count = requested_units if isinstance(requested_units, int) and requested_units > 0 else 3
    default_days = ["Monday", "Wednesday", "Friday", "Saturday", "Sunday"]
    focuses = ["cardio", "boxing", "strength", "recovery", "mobility"]
    default_durations = [60, 55, 45, 40, 50]

    units = []
    for i in range(unit_count):
        focus = focuses[i % len(focuses)]
        day_label = default_days[i % len(default_days)]
        duration_limit = default_durations[i % len(default_durations)]
        units.append({
            "title": f"{focus.capitalize()} Session",
            "focus_type": focus,
            "day_label": day_label,
            "duration_limit_mins": duration_limit,
            "draft": {
                "goal": "Build balanced weekly fitness",
                "duration_limit_mins": duration_limit,
                "workout_name": f"{focus.capitalize()} Session",
                "difficulty": "intermediate",
                "focus_type": focus,
                "session_outline": [
                    {"section": "warmup", "minutes": 10, "items": ["Dynamic mobility", "Pulse raiser"]},
                    {"section": "main", "minutes": max(duration_limit - 15, 15), "items": ["Main work block"]},
                    {"section": "cooldown", "minutes": 5, "items": ["Breathing", "Stretching"]},
                ],
                "exercise_details": [
                    {"name": "Primary block", "sets": 3, "reps": "8-12", "rest_seconds": 60, "notes": "Adjust by RPE"}
                ],
                "safety_notes": ["Keep effort controlled and pain-free"],
            },
        })

    return {
        "response": "Created a weekly routine with multiple workout units.",
        "routine_draft": {
            "routine_name": "Weekly Workout Routine",
            "goal": "Balanced weekly development" + (f" ({schedule_guidance})" if schedule_guidance else ""),
            "units": units,
        },
    }


def execute_weekly_routine_task(user_query, shared_context, step_tracer, current_routine=None):
    """Generates a weekly routine composed of multiple workout units."""
    schedule_guidance = _build_schedule_guidance(shared_context)
    requested_units = _extract_requested_unit_count(user_query)
    routine_operation = _infer_routine_operation(user_query)
    rag_result = _fetch_workout_rag_context(user_query)

    sys_prompt = """You are the Nutrissistant Workout Agent.
Generate a single JSON object only for a weekly routine composed of multiple workout units.

Return exactly this schema:
{
  "response": "short natural language response for the user",
  "routine_draft": {
    "routine_name": "string",
    "goal": "string",
    "units": [
      {
        "title": "string",
        "focus_type": "cardio|boxing|strength|recovery|mobility|mixed",
        "day_label": "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Flexible",
        "duration_limit_mins": 60,
        "draft": {
          "goal": "string",
          "duration_limit_mins": 60,
          "workout_name": "string",
          "difficulty": "beginner|intermediate|advanced",
          "focus_type": "string",
          "session_outline": [
            {
              "section": "warmup|main|cooldown",
              "minutes": 10,
              "items": ["string", "string"]
            }
          ],
          "exercise_details": [
            {
              "name": "string",
              "sets": 3,
              "reps": "8-12",
              "rest_seconds": 60,
              "notes": "string"
            }
          ],
          "safety_notes": ["string"]
        }
      }
    ]
  }
}

Rules:
1. Always return a full routine with units (never return a standalone workout).
2. If current_routine is provided and routine_operation is "update", treat it as the base plan.
3. For updates, keep unaffected units unless the query asks for a full redesign.
4. Duration can vary per unit. Do NOT force one duration on all units.
5. If the query mentions a specific duration for one activity (e.g., "45 minute run"), apply it only to that relevant unit.
6. Use schedule_context only as soft guidance; it is not a strict numeric cap.
7. If requested_units is provided, match it exactly.
8. If requested_units is missing and current_routine exists, keep roughly the same number of units unless query implies otherwise.
9. Use retrieved_context as evidence when relevant. Do not invent citations.
10. Never mention backend identifiers or provenance tokens in user-facing text (no PMCID, source numbers, or similarity scores).
11. Phrase evidence naturally, for example: "research indicates", "it is recommended", or "based on best practices".
"""

    user_prompt = json.dumps({
        "query": user_query,
        "routine_operation": routine_operation,
        "schedule_context": schedule_guidance,
        "requested_units": requested_units,
        "current_routine": current_routine if isinstance(current_routine, dict) else None,
        "retrieval": {
            "status": rag_result.get("status"),
            "reason": rag_result.get("reason"),
            "sources": rag_result.get("sources", [])
        },
        "retrieved_context": rag_result.get("context", "")
    })

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]

    try:
        response = json_llm.invoke(messages)
        raw_content = response.content
        if isinstance(raw_content, list):
            raw_content = "".join(
                block if isinstance(block, str) else block.get("text", "")
                for block in raw_content
            )
        parsed = json.loads(raw_content)

        routine_draft = parsed.get("routine_draft")
        if not isinstance(routine_draft, dict):
            raise ValueError("routine_draft missing or invalid")

        units = routine_draft.get("units")
        if not isinstance(units, list) or not units:
            raise ValueError("routine_draft.units missing or invalid")

        for unit in units:
            if not isinstance(unit, dict):
                continue
            # Keep model-provided per-unit duration semantics; only add a fallback default if missing.
            unit_duration = unit.get("duration_limit_mins")
            if not isinstance(unit_duration, int) or unit_duration <= 0:
                unit["duration_limit_mins"] = 60

            draft = unit.get("draft")
            if isinstance(draft, dict):
                draft_duration = draft.get("duration_limit_mins")
                if not isinstance(draft_duration, int) or draft_duration <= 0:
                    draft["duration_limit_mins"] = unit.get("duration_limit_mins", 60)

        response_text = parsed.get("response") or "Created a weekly routine with multiple workout units."
        response_text = _sanitize_user_text(response_text)
        routine_draft = _sanitize_user_payload(routine_draft)

        step_tracer.append({
            "module": MODULE_NAME,
            "prompt": {"system": sys_prompt, "user": user_prompt},
            "response": {
                **parsed,
                "retrieval": {
                    "status": rag_result.get("status"),
                    "reason": rag_result.get("reason"),
                    "sources": rag_result.get("sources", [])
                }
            }
        })

        return {
            "response": response_text,
            "routine_draft": routine_draft
        }

    except Exception:
        fallback = _fallback_weekly_routine(user_query, requested_units, schedule_guidance=schedule_guidance)
        step_tracer.append({
            "module": MODULE_NAME,
            "prompt": {"system": sys_prompt, "user": user_prompt},
            "response": {
                "error": "weekly_model_output_invalid_or_failed",
                "retrieval": {
                    "status": rag_result.get("status"),
                    "reason": rag_result.get("reason"),
                    "sources": rag_result.get("sources", [])
                },
                "fallback": fallback
            }
        })
        fallback["response"] = _sanitize_user_text(fallback.get("response", ""))
        fallback["routine_draft"] = _sanitize_user_payload(fallback.get("routine_draft", {}))
        return fallback
