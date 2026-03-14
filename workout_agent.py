import os
import json
import re
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_core.messages import SystemMessage, HumanMessage
from pinecone import Pinecone
from pydantic import SecretStr

import state_manager

# Load environment variables
load_dotenv()

LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME_WORKOUTS = os.getenv("PINECONE_INDEX_NAME_WORKOUTS")
MODEL_NAME = "RPRTHPB-gpt-5-mini"
EMBEDDING_MODEL = "RPRTHPB-text-embedding-3-small"
MODULE_NAME = "WorkoutAgent"
PIPELINE_SIMPLE_RAG = "simple_rag"
PIPELINE_REFLECTION = "reflection"
MAX_REFLECTION_LOOPS = 3

json_llm = ChatOpenAI(
    api_key=SecretStr(LLMOD_API_KEY or ""),
    base_url=OPENAI_API_BASE,
    model=MODEL_NAME,
).bind(response_format={"type": "json_object"})

critique_json_llm = ChatOpenAI(
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
    
    # Look for explicit phrases like "3 workouts", "two sessions", "4 days"
    pattern = r"\b(two|three|four|five|six|seven|[2-7])\s*(?:workouts?|sessions?|days?|times)\b"
    match = re.search(pattern, q)
    
    if match:
        val = match.group(1)
        keyword_to_num = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
        # Return the mapped word, or the integer if it was a digit
        return keyword_to_num.get(val, int(val) if val.isdigit() else None)

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


def _build_generation_system_prompt():
    return """You are the Nutrissistant Workout Agent.
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
12. When pipeline_mode is "simple_rag", prioritize minimal targeted edits for update requests.
13. When pipeline_mode is "reflection", return a complete cohesive full-week routine.
14. You MUST strictly adhere to the `user_context` provided in the payload. Do not include exercises requiring equipment the user does not have. You must modify the routine to accommodate any listed injuries or restrictions.
"""


def _build_generation_payload(
    user_query,
    routine_operation,
    schedule_guidance,
    requested_units,
    current_routine,
    rag_result,
    pipeline_mode,
    user_context=None,
    reflection_feedback=None,
    initial_draft=None,
):
    payload = {
        "query": user_query,
        "routine_operation": routine_operation,
        "schedule_context": schedule_guidance,
        "user_context": user_context or {},
        "requested_units": requested_units,
        "current_routine": current_routine if isinstance(current_routine, dict) else None,
        "retrieval": {
            "status": rag_result.get("status"),
            "reason": rag_result.get("reason"),
            "sources": rag_result.get("sources", [])
        },
        "retrieved_context": rag_result.get("context", ""),
        "pipeline_mode": pipeline_mode,
    }
    if isinstance(initial_draft, dict):
        payload["initial_draft"] = initial_draft
    if isinstance(reflection_feedback, list) and reflection_feedback:
        payload["reflection_feedback"] = reflection_feedback
    return payload


def _run_generation_stage(stage_name, sys_prompt, payload, step_tracer, rag_result):
    user_prompt = json.dumps(payload)
    response = json_llm.invoke([
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ])
    parsed = _parse_json_response_content(response.content)

    step_tracer.append({
        "module": MODULE_NAME,
        "stage": stage_name,
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

    return parsed


def _run_critique_stage(
    user_query,
    routine_operation,
    schedule_guidance,
    requested_units,
    current_routine,
    candidate_result,
    step_tracer,
    iteration,
    user_context=None,
):
    sys_prompt = """You are a strict workout-plan critic.
Review a generated weekly routine and return JSON only with this schema:
{
    "needs_refinement": true,
  "valid": true,
  "critical_issues": ["string"],
  "suggested_edits": ["string"],
  "summary": "string"
}

Rules:
1. Focus only on objective checks: structural schema consistency, safety contradictions, unrealistic overload, and conflicts with explicit user intent.
2. Do NOT give subjective style feedback.
3. Keep suggested_edits actionable and concise.
4. Set needs_refinement=false only if no further refinement is needed at all.
5. If no critical issues, set valid=true, critical_issues=[], and suggested_edits=[].
"""

    critique_payload = {
        "query": user_query,
        "routine_operation": routine_operation,
        "schedule_context": schedule_guidance,
        "user_context": user_context or {},
        "requested_units": requested_units,
        "current_routine": current_routine if isinstance(current_routine, dict) else None,
        "candidate_result": candidate_result,
    }
    user_prompt = json.dumps(critique_payload)

    response = critique_json_llm.invoke([
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ])
    critique = _parse_json_response_content(response.content)

    step_tracer.append({
        "module": MODULE_NAME,
        "stage": f"reflection_critique_{iteration}",
        "prompt": {"system": sys_prompt, "user": user_prompt},
        "response": critique,
    })

    return critique


def _should_continue_reflection(critique):
    if not isinstance(critique, dict):
        return False

    needs_refinement = critique.get("needs_refinement")
    if isinstance(needs_refinement, bool):
        return needs_refinement

    critical_issues = critique.get("critical_issues")
    return isinstance(critical_issues, list) and len(critical_issues) > 0


def _normalize_and_validate_routine(parsed):
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
    return response_text, routine_draft


def execute_weekly_routine_task(user_query, shared_context, step_tracer, current_routine=None):
    """Generates a weekly routine composed of multiple workout units."""
    state = state_manager.load_state()
    user_context = {
        "profile": state.get("user_profile", ""),
        "equipment": state.get("equipment", []),
        "injuries": state.get("injuries", []),
        "restrictions": state.get("general_workout_restrictions", [])
    }

    schedule_guidance = _build_schedule_guidance(shared_context)
    requested_units = _extract_requested_unit_count(user_query)
    routine_operation = _infer_routine_operation(user_query)
    rag_result = _fetch_workout_rag_context(user_query)

    requested_pipeline = None
    if isinstance(shared_context, dict):
        requested_pipeline = shared_context.get("workout_pipeline")

    if requested_pipeline in (PIPELINE_SIMPLE_RAG, PIPELINE_REFLECTION):
        pipeline_mode = requested_pipeline
    else:
        pipeline_mode = PIPELINE_REFLECTION if routine_operation == "create" else PIPELINE_SIMPLE_RAG

    if routine_operation == "update":
        pipeline_mode = PIPELINE_SIMPLE_RAG

    sys_prompt = _build_generation_system_prompt()

    try:
        base_payload = _build_generation_payload(
            user_query=user_query,
            routine_operation=routine_operation,
            schedule_guidance=schedule_guidance,
            requested_units=requested_units,
            current_routine=current_routine,
            rag_result=rag_result,
            pipeline_mode=pipeline_mode,
            user_context=user_context,
        )

        if pipeline_mode == PIPELINE_REFLECTION:
            parsed = _run_generation_stage(
                stage_name="reflection_generation",
                sys_prompt=sys_prompt,
                payload=base_payload,
                step_tracer=step_tracer,
                rag_result=rag_result,
            )

            for iteration in range(1, MAX_REFLECTION_LOOPS + 1):
                critique = None
                try:
                    critique = _run_critique_stage(
                        user_query=user_query,
                        routine_operation=routine_operation,
                        schedule_guidance=schedule_guidance,
                        requested_units=requested_units,
                        current_routine=current_routine,
                        candidate_result=parsed,
                        step_tracer=step_tracer,
                        iteration=iteration,
                        user_context=user_context,
                    )
                except Exception as critique_error:
                    step_tracer.append({
                        "module": MODULE_NAME,
                        "stage": f"reflection_critique_error_{iteration}",
                        "response": {"error": str(critique_error)}
                    })
                    break

                if not _should_continue_reflection(critique):
                    step_tracer.append({
                        "module": MODULE_NAME,
                        "stage": f"reflection_stop_{iteration}",
                        "response": {
                            "reason": "critic_marked_no_further_refinement_needed"
                        }
                    })
                    break

                refine_payload = _build_generation_payload(
                    user_query=user_query,
                    routine_operation=routine_operation,
                    schedule_guidance=schedule_guidance,
                    requested_units=requested_units,
                    current_routine=current_routine,
                    rag_result=rag_result,
                    pipeline_mode=pipeline_mode,
                    user_context=user_context,
                    reflection_feedback=critique.get("suggested_edits", []) if isinstance(critique, dict) else [],
                    initial_draft=parsed.get("routine_draft") if isinstance(parsed, dict) else None,
                )
                parsed = _run_generation_stage(
                    stage_name=f"reflection_refinement_{iteration}",
                    sys_prompt=sys_prompt,
                    payload=refine_payload,
                    step_tracer=step_tracer,
                    rag_result=rag_result,
                )
        else:
            parsed = _run_generation_stage(
                stage_name="simple_rag_generation",
                sys_prompt=sys_prompt,
                payload=base_payload,
                step_tracer=step_tracer,
                rag_result=rag_result,
            )

        response_text, routine_draft = _normalize_and_validate_routine(parsed)

        return {
            "response": response_text,
            "routine_draft": routine_draft
        }

    except Exception as e:
        fallback = _fallback_weekly_routine(user_query, requested_units, schedule_guidance=schedule_guidance)
        step_tracer.append({
            "module": MODULE_NAME,
            "stage": "pipeline_fallback",
            "response": {
                "pipeline_mode": pipeline_mode,
                "error": "weekly_model_output_invalid_or_failed",
                "details": str(e),
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