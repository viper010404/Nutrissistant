"""
Meal Planner Agent – entry point.

This agent:
1) Pulls nutrition evidence from the nutrition vector DB (RAG).
2) Reads user data and extracts constraints (allergies, dietary restrictions, etc.).
3) Plans meals and components.
4) Delegates component recipe generation to the Recipe Extractor.
5) Returns a structured meal-plan JSON output.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from typing import Any

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(CURRENT_DIR)
SRC_DIR = os.path.dirname(AGENTS_DIR)
PROJECT_ROOT = os.path.dirname(SRC_DIR)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from importlib import import_module

prompts_module = import_module("src.agents.meal_planner.prompts")
query_db_module = import_module("src.utils.query_DB")
llm_utils_module = import_module("src.utils.LLM_utils")
tools_module = import_module("src.agents.meal_planner.tools")
recipe_extractor_module = import_module("src.agents.recipe_extractor.main")

MAX_REFLECTION_LOOPS = 2


def _normalize_components(meal: dict) -> list:
    components = meal.get("components") if isinstance(meal.get("components"), list) else []
    normalized = []
    for comp in components:
        if isinstance(comp, dict):
            normalized.append(comp)
    if not normalized:
        normalized = [
            {
                "component": "main_course",
                "description": meal.get("description", "Create a balanced dish."),
                "free_time_mins": 45,
                "nutritional_targets_min": {},
                "nutritional_targets_max": {},
                "constraints": {},
                "user_preferences": {},
            }
        ]
    return normalized


def _normalize_meal_blueprint(raw_blueprint: dict) -> dict:
    if not isinstance(raw_blueprint, dict):
        return {"response": "Prepared your meal plan.", "date": date.today().isoformat(), "meals": []}

    meals = raw_blueprint.get("meals", [])
    if not isinstance(meals, list):
        meals = []

    normalized_meals = []
    for meal in meals:
        if not isinstance(meal, dict):
            continue
        normalized_meals.append(
            {
                "meal_id": meal.get("meal_id"),
                "meal_type": meal.get("meal_type", "dinner"),
                "day_of_week": meal.get("day_of_week", "Monday"),
                "description": meal.get("description", ""),
                "warnings": meal.get("warnings", []) if isinstance(meal.get("warnings"), list) else [],
                "suggestions": meal.get("suggestions", []) if isinstance(meal.get("suggestions"), list) else [],
                "components": _normalize_components(meal),
            }
        )

    return {
        "response": raw_blueprint.get("response", "Prepared your meal plan."),
        "date": raw_blueprint.get("date", date.today().isoformat()),
        "meals": normalized_meals,
    }


def _run_meal_plan_critique(
    query: str,
    user_context: dict,
    rag_result: dict,
    optional_task_context: dict,
    shared_context: dict,
    candidate_blueprint: dict,
    step_tracer: list | None,
    iteration: int,
) -> dict:
    critique_payload = {
        "query": query,
        "optional_task_context": optional_task_context or {},
        "shared_context": shared_context or {},
        "user_context": user_context or {},
        "retrieval": {
            "status": rag_result.get("status"),
            "reason": rag_result.get("reason"),
            "sources": rag_result.get("sources", []),
        },
        "retrieved_context": rag_result.get("context", ""),
        "candidate_result": candidate_blueprint if isinstance(candidate_blueprint, dict) else {},
    }

    critique = llm_utils_module._invoke_json_llm(
        prompts_module.MEAL_PLANNER_CRITIQUE_PROMPT,
        critique_payload,
        step_tracer=step_tracer,
        module_name="MealPlannerReflectionCritic",
    )

    # if step_tracer is not None and hasattr(step_tracer, "append"):
    #     step_tracer.append(
    #         {
    #             "module": "MealPlanner",
    #             "stage": f"meal_reflection_critique_{iteration}",
    #             "response": critique,
    #         }
    #     )

    return critique if isinstance(critique, dict) else {}


def _should_refine(critique: dict) -> bool:
    if not isinstance(critique, dict):
        return False
    needs_refinement = critique.get("needs_refinement")
    if isinstance(needs_refinement, bool):
        return needs_refinement
    issues = critique.get("critical_issues")
    return isinstance(issues, list) and len(issues) > 0


def run_meal_planner(task: str | dict, step_tracer: list | None = None, shared_context: dict | None = None) -> dict:
    """
    Execute meal planning workflow and return the final plan.

    Parameters
    ----------
    task : str | dict
        User request string or payload dict with keys like:
        - query: str
        - optional_task_context: dict
    step_tracer : list, optional
        Trace collector for prompt/response/tool steps.
    shared_context : dict, optional
        Context from supervisor/scheduler.

    Returns
    -------
    dict
        Structured meal plan JSON payload.
    """
    shared_context = shared_context or {}
    optional_task_context = {}

    if isinstance(task, str):
        query = task
    elif isinstance(task, dict):
        query = task.get("query") or task.get("task") or "Plan my meals."
        optional_task_context = task.get("optional_task_context", {}) if isinstance(task.get("optional_task_context"), dict) else {}
    else:
        query = "Plan my meals."

    state = tools_module.load_user_state()
    user_context = tools_module.extract_relevant_user_context(state)

    rag_query = f"{query}\nUser profile: {user_context.get('user_profile', '')}"
    rag_result = tools_module.fetch_nutrition_rag_context(rag_query)

    # if step_tracer is not None and hasattr(step_tracer, "append"):
    #     step_tracer.append(
    #         {
    #             "module": "MealPlanner",
    #             "stage": "nutrition_rag",
    #             "query": rag_query,
    #             "retrieval": {
    #                 "status": rag_result.get("status"),
    #                 "reason": rag_result.get("reason"),
    #                 "sources": rag_result.get("sources", []),
    #             },
    #         }
    #     )

    raw_blueprint = tools_module.generate_meal_blueprint(
        query=query,
        user_context=user_context,
        rag_result=rag_result,
        optional_task_context=optional_task_context,
        shared_context=shared_context,
        step_tracer=step_tracer,
    )

    enable_reflection = bool(optional_task_context.get("enable_meal_reflection", False))
    if enable_reflection:
        for iteration in range(1, MAX_REFLECTION_LOOPS + 1):
            critique = _run_meal_plan_critique(
                query=query,
                user_context=user_context,
                rag_result=rag_result,
                optional_task_context=optional_task_context,
                shared_context=shared_context,
                candidate_blueprint=raw_blueprint,
                step_tracer=step_tracer,
                iteration=iteration,
            )

            if not _should_refine(critique):
                break

            refine_payload = {
                **optional_task_context,
                "pipeline_mode": "reflection",
                "initial_draft": raw_blueprint if isinstance(raw_blueprint, dict) else {},
                "reflection_feedback": critique.get("suggested_edits", []) if isinstance(critique, dict) else [],
                # Explicit guard: reflection is for meal blueprint only, never recipe-level revisions.
                "reflect_meal_plan_only": True,
            }

            raw_blueprint = tools_module.generate_meal_blueprint(
                query=query,
                user_context=user_context,
                rag_result=rag_result,
                optional_task_context=refine_payload,
                shared_context=shared_context,
                step_tracer=step_tracer,
            )

            # if step_tracer is not None and hasattr(step_tracer, "append"):
            #     step_tracer.append(
            #         {
            #             "module": "MealPlanner",
            #             "stage": f"meal_reflection_refinement_{iteration}",
            #             "response": raw_blueprint,
            #         }
            #     )

    blueprint = _normalize_meal_blueprint(raw_blueprint)

    final_meals = []
    for meal in blueprint.get("meals", []):
        meal_warnings = list(meal.get("warnings", []))
        meal_suggestions = list(meal.get("suggestions", []))
        dishes = []

        for component_plan in meal.get("components", []):
            extractor_input = tools_module.build_recipe_extractor_input(
                meal_plan_item=meal,
                component_plan=component_plan,
                user_context=user_context,
                optional_task_context=optional_task_context,
            )

            # if step_tracer is not None and hasattr(step_tracer, "append"):
            #     step_tracer.append(
            #         {
            #             "module": "MealPlanner",
            #             "stage": "recipe_extractor_input",
            #             "payload": extractor_input,
            #         }
            #     )

            recipe_result = recipe_extractor_module.run_recipe_extractor(extractor_input, step_tracer=step_tracer)
            recipe, warnings, suggestions = tools_module.extract_recipe_from_result(recipe_result)
            meal_warnings.extend(warnings)
            meal_suggestions.extend(suggestions)
            # --- NEW SAFETY NET: Prevent silent failures ---
            if not isinstance(recipe, dict) or not recipe.get("name"):
                import uuid
                recipe = {
                    "recipe_id": f"fallback_{uuid.uuid4().hex[:8]}",
                    "name": component_plan.get("description", "Agent Custom Dish"),
                    "description": "The agent planned this meal, but the recipe details failed to generate properly due to an AI timeout.",
                    "prep_time_mins": component_plan.get("free_time_mins", 30),
                    "ingredients": [],
                    "instructions": ["Ask Nutrissistant to specifically generate a recipe for this dish!"],
                    "nutrition_per_serving": {}
                }

            # Keep the entire recipe object, just ensure the component label is correct
            recipe["component"] = component_plan.get("component", "main_course")
            dishes.append(recipe)
            # if isinstance(recipe, dict):
            #     dishes.append({
            #         "recipe_id": recipe.get("recipe_id") or recipe.get("id"),
            #         "name": recipe.get("name", "Unnamed Dish"),
            #         "component": component_plan.get("component", "main_course"),
            #     })

        final_meals.append(
            {
                "meal_id": meal.get("meal_id") or f"meal_{meal.get('day_of_week', 'day').lower()}_{meal.get('meal_type', 'meal')}",
                "meal_type": meal.get("meal_type", "dinner"),
                "day_of_week": meal.get("day_of_week", "Monday"),
                "dishes": dishes,
                "warnings": meal_warnings,
                "suggestions": meal_suggestions,
            }
        )

    final_output = tools_module.build_final_meal_output(blueprint.get("date"), final_meals)

    # if step_tracer is not None and hasattr(step_tracer, "append"):
    #     step_tracer.append({"module": "MealPlanner", "stage": "final_output", "response": final_output})

    return final_output


def execute_weekly_meal_task(
    user_query: str,
    shared_context: dict | None = None,
    step_tracer: list | None = None,
    optional_task_context: dict | None = None,
    ) -> dict:
    """Supervisor-friendly wrapper for meal planning tasks."""
    task_payload = {
        "query": user_query,
        "optional_task_context": optional_task_context or {},
    }
    meal_plan = run_meal_planner(task_payload, step_tracer=step_tracer, shared_context=shared_context)
    return {
        "response": "Prepared your meal plan.",
        "meal_plan": meal_plan,
    }        


if __name__ == "__main__":
    import traceback

    print("=" * 60)
    print("Meal Planner – quick smoke test (1 meal, 1 component)")
    print("=" * 60)

    steps: list[dict[str, Any]] = []

    # Single-meal task so only one recipe-extractor call is made
    task = {
        "query": "Plan a healthy dinner for tonight",
        "optional_task_context": {"max_meals": 1},
    }

    try:
        result = run_meal_planner(task, step_tracer=steps)
    except Exception as exc:
        print(f"\n[FAILED] run_meal_planner raised: {exc}")
        traceback.print_exc()
        sys.exit(1)

    # ── Summary ──────────────────────────────────────────────────────
    meals = result.get("meals", [])
    print(f"\n✓ Date      : {result.get('date')}")
    print(f"✓ Meals     : {len(meals)}")
    for meal in meals:
        dishes = meal.get("dishes", [])
        print(f"  [{meal.get('day_of_week')} {meal.get('meal_type')}]  dishes={len(dishes)}")
        for dish in dishes:
            print(f"    • {dish.get('name')}  (id={dish.get('recipe_id')})")
        if meal.get("warnings"):
            print(f"    warnings : {meal['warnings']}")
        if meal.get("suggestions"):
            print(f"    suggestions: {meal['suggestions'][:1]}")

    # ── RAG trace ────────────────────────────────────────────────────
    rag_step = next((s for s in steps if s.get("stage") == "nutrition_rag"), None)
    if rag_step:
        retrieval = rag_step.get("retrieval", {})
        print(f"\n✓ RAG status: {retrieval.get('status')}  sources={len(retrieval.get('sources', []))}")

    print("\n── Full JSON output ──")
    print(json.dumps(result, ensure_ascii=False, indent=2))

