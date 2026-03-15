from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import date

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from pinecone import Pinecone
from pydantic import SecretStr

from src.config import RECIPE_EMBED_MODEL_DEFAULT

# Ensure project root is importable when this file is executed directly.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(CURRENT_DIR)
SRC_DIR = os.path.dirname(AGENTS_DIR)
PROJECT_ROOT = os.path.dirname(SRC_DIR)
if PROJECT_ROOT not in sys.path:
	sys.path.insert(0, PROJECT_ROOT)

from src.core import state_manager
from src.agents.meal_planner import prompts as prompts_module
from src.utils import LLM_utils as llm_utils_module
from src.utils.LLM_utils import LLMOD_API_KEY, OPENAI_API_BASE


load_dotenv()

MODULE_NAME = "MealPlanner"
EXTRACTED_RECIPES_PATH = os.path.join(PROJECT_ROOT, "src", "agents", "meal_planner", "extracted_recipes.json")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME_NUTRITION = os.getenv("PINECONE_INDEX_NAME_NUTRITION")
EMBEDDING_MODEL = os.getenv("NUTRITION_VECTOR_EMBED_MODEL", RECIPE_EMBED_MODEL_DEFAULT)



def load_user_state() -> dict:
	state = state_manager.load_state()
	return state if isinstance(state, dict) else {}


def extract_relevant_user_context(state: dict) -> dict:
	if not isinstance(state, dict):
		state = {}

	context = {
		"allergies": state.get("allergies", []) if isinstance(state.get("allergies"), list) else [],
		"dietary_restrictions": state.get("dietary_restrictions", []) if isinstance(state.get("dietary_restrictions"), list) else [],
		"general_meal_restrictions": state.get("general_meal_restrictions", []) if isinstance(state.get("general_meal_restrictions"), list) else [],
		"user_profile": state.get("user_profile", "") if isinstance(state.get("user_profile"), str) else "",
		"available_ingredients": [],
	}

	profile_text = context["user_profile"].lower()
	if "beginner" in profile_text:
		context["cooking_skill"] = "beginner"
	elif "advanced" in profile_text:
		context["cooking_skill"] = "advanced"
	elif "intermediate" in profile_text:
		context["cooking_skill"] = "intermediate"

	return context


def fetch_nutrition_rag_context(query: str, top_k: int = 5, max_chunk_chars: int = 1200) -> dict:
	if not query:
		return {"status": "skipped", "reason": "empty_query", "context": "", "sources": []}

	if not (PINECONE_API_KEY and PINECONE_INDEX_NAME_NUTRITION and LLMOD_API_KEY and OPENAI_API_BASE):
		return {"status": "disabled", "reason": "missing_env", "context": "", "sources": []}

	try:
		pc = Pinecone(api_key=PINECONE_API_KEY)
		index = pc.Index(PINECONE_INDEX_NAME_NUTRITION)

		embeddings = OpenAIEmbeddings(
			api_key=SecretStr(LLMOD_API_KEY or ""),
			base_url=OPENAI_API_BASE,
			model=EMBEDDING_MODEL,
		)
		query_vector = embeddings.embed_query(query)

		query_result = index.query(vector=query_vector, top_k=top_k, include_metadata=True)
		matches = query_result["matches"] if isinstance(query_result, dict) else getattr(query_result, "matches", [])

		if not matches:
			return {"status": "ok", "reason": "no_matches", "context": "", "sources": []}

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
			sources.append({"pmcid": pmcid, "score": score_value})

			src = f"Source {i} | PMCID: {pmcid}"
			if score_value is not None:
				src += f" | score: {score_value}"
			context_blocks.append(f"{src}\n{chunk_text}")

		return {
			"status": "ok",
			"reason": "retrieved",
			"context": "\n\n".join(context_blocks),
			"sources": sources,
		}
	except Exception as exc:  # noqa: BLE001
		return {"status": "error", "reason": str(exc), "context": "", "sources": []}


def generate_meal_blueprint(
	query: str,
	user_context: dict,
	rag_result: dict,
	optional_task_context: dict | None = None,
	shared_context: dict | None = None,
	step_tracer: list | None = None,
) -> dict:
	payload = {
		"query": query,
		"optional_task_context": optional_task_context or {},
		"shared_context": shared_context or {},
		"pipeline_mode": (optional_task_context or {}).get("pipeline_mode", "simple"),
		"initial_draft": (optional_task_context or {}).get("initial_draft"),
		"reflection_feedback": (optional_task_context or {}).get("reflection_feedback", []),
		"user_context": user_context or {},
		"retrieval": {
			"status": rag_result.get("status"),
			"reason": rag_result.get("reason"),
			"sources": rag_result.get("sources", []),
		},
		"retrieved_context": rag_result.get("context", ""),
	}

	result = llm_utils_module._invoke_json_llm(
		prompts_module.MAIN_MEAL_PLANNER_SYSTEM_PROMPT,
		payload,
		step_tracer=step_tracer,
		module_name=MODULE_NAME,
	)

	if not isinstance(result, dict):
		return {
			"response": "Prepared a meal plan.",
			"date": date.today().isoformat(),
			"meals": [],
		}

	result.setdefault("response", "Prepared a meal plan.")
	result.setdefault("date", date.today().isoformat())
	result.setdefault("meals", [])
	if not isinstance(result.get("meals"), list):
		result["meals"] = []
	return result


def _normalize_meal_type(meal_type: str) -> str:
	val = (meal_type or "").strip().lower()
	if val in {"breakfast", "lunch", "dinner", "snack"}:
		return val
	return "dinner"


def _normalize_course(component: str) -> str:
	val = (component or "").strip().lower()
	if val in {"main_course", "side_dish", "dessert", "any"}:
		return val
	return "main_course"


def _safe_number(value, default):
	try:
		if value is None:
			return default
		return int(value)
	except Exception:  # noqa: BLE001
		return default


def build_recipe_extractor_input(
	meal_plan_item: dict,
	component_plan: dict,
	user_context: dict,
	optional_task_context: dict | None = None,
) -> dict:
	meal_id = meal_plan_item.get("meal_id") or f"meal_{uuid.uuid4().hex[:10]}"
	meal_type = _normalize_meal_type(meal_plan_item.get("meal_type"))
	day_of_week = meal_plan_item.get("day_of_week") or "Monday"

	component = _normalize_course(component_plan.get("component"))
	description_parts = [
		meal_plan_item.get("description", ""),
		component_plan.get("description", ""),
	]
	task_note = (optional_task_context or {}).get("note")
	if isinstance(task_note, str) and task_note.strip():
		description_parts.append(task_note.strip())
	description = ". ".join(part.strip() for part in description_parts if isinstance(part, str) and part.strip())

	constraints_in = component_plan.get("constraints") if isinstance(component_plan.get("constraints"), dict) else {}
	excluded = []
	for key in ("allergies", "dietary_restrictions", "general_meal_restrictions"):
		vals = user_context.get(key, [])
		if isinstance(vals, list):
			excluded.extend(vals)
	comp_excluded = constraints_in.get("excluded_ingredients", [])
	if isinstance(comp_excluded, list):
		excluded.extend(comp_excluded)

	constraints = {
		"dietary_restrictions": user_context.get("dietary_restrictions", []),
		"allergies": user_context.get("allergies", []),
		"excluded_ingredients": sorted(list({str(v).strip() for v in excluded if str(v).strip()})),
		"available_ingredients": constraints_in.get("available_ingredients", user_context.get("available_ingredients", [])) or [],
		"must_have_all_ingredients": bool(constraints_in.get("must_have_all_ingredients", False)),
		"max_ingredients": _safe_number(constraints_in.get("max_ingredients"), 12),
	}

	user_preferences_in = component_plan.get("user_preferences") if isinstance(component_plan.get("user_preferences"), dict) else {}
	user_preferences = {
		"cooking_skill": user_preferences_in.get("cooking_skill") or user_context.get("cooking_skill", "beginner"),
		"prefer_simple_recipes": bool(user_preferences_in.get("prefer_simple_recipes", user_context.get("prefer_simple_recipes", True))),
		"spice_tolerance": user_preferences_in.get("spice_tolerance") or user_context.get("spice_tolerance", "medium"),
	}

	return {
		"request_type": "generate",
		"meal_context": {
			"meal_id": meal_id,
			"meal_type": meal_type,
			"day_of_week": day_of_week,
			"free_time_mins": _safe_number(component_plan.get("free_time_mins"), 45),
			"description": description or f"Create a {component.replace('_', ' ')} for {meal_type}.",
			"course": component,
			"components": [component],
		},
		"nutritional_targets_min": component_plan.get("nutritional_targets_min", {}) if isinstance(component_plan.get("nutritional_targets_min"), dict) else {},
		"nutritional_targets_max": component_plan.get("nutritional_targets_max", {}) if isinstance(component_plan.get("nutritional_targets_max"), dict) else {},
		"constraints": constraints,
		"revision_context": {
			"original_recipe_id": None,
			"original_recipe_name": None,
			"feedback": "",
			"keep_base_recipe": False,
		},
		"user_preferences": user_preferences,
	}


def extract_recipe_from_result(recipe_result: dict) -> tuple[dict | None, list, list]:
	if not isinstance(recipe_result, dict):
		return None, ["Recipe extractor returned invalid output."], []

	recipe = recipe_result.get("recipe") if isinstance(recipe_result.get("recipe"), dict) else None
	warnings = recipe_result.get("warnings", []) if isinstance(recipe_result.get("warnings"), list) else []
	suggestions = recipe_result.get("suggestions", []) if isinstance(recipe_result.get("suggestions"), list) else []

	if recipe is None:
		message = recipe_result.get("message") or "Recipe extractor did not return a recipe."
		warnings = [*warnings, str(message)]
		print(f"[MealPlanner] Extractor returned no recipe. status={recipe_result.get('status')} message={message}")
		print(f"[MealPlanner] Full extractor result: {json.dumps(recipe_result, ensure_ascii=False, default=str)[:500]}")
	else:
		add_recipe_to_json(recipe, EXTRACTED_RECIPES_PATH)
	return recipe, warnings, suggestions

def add_recipe_to_json(recipe: dict, filename: str) -> None:
	"""Save a recipe dictionary into a JSON list file (deduplicated by recipe_id)."""
	target_path = os.path.abspath(EXTRACTED_RECIPES_PATH)
	requested_path = os.path.abspath(filename) if filename else target_path
	if requested_path != target_path:
		print(f"[MealPlanner] WARNING: Ignoring non-canonical path: {requested_path}")
	filename = target_path
	print(f"[MealPlanner] Attempting to save recipe to {filename}...")
	try:
		if os.path.exists(filename):
			with open(filename, "r", encoding="utf-8") as file:
				raw = file.read().strip()
			if not raw:
				data = []
			else:
				try:
					data = json.loads(raw)
				except json.JSONDecodeError:
					print(f"[MealPlanner] WARNING: Invalid JSON in {filename}; resetting file to [].")
					data = []
		else:
			data = []

		if not isinstance(data, list):
			data = []

		if not isinstance(recipe, dict):
			return

		recipe_id = recipe.get("recipe_id") or recipe.get("id")
		if not recipe_id:
			recipe_id = f"recipe_{uuid.uuid4().hex}"
			recipe["recipe_id"] = recipe_id

		known_ids = {
			(item.get("recipe_id") or item.get("id"))
			for item in data
			if isinstance(item, dict)
		}

		if recipe_id not in known_ids:
			data.append(recipe)

		with open(filename, "w", encoding="utf-8") as file:
			json.dump(data, file, indent=2, ensure_ascii=False)
	except Exception as e:
		print(e)
		return

def build_final_meal_output(plan_date: str, meal_entries: list) -> dict:
	return {
		"date": plan_date or date.today().isoformat(),
		"meals": meal_entries if isinstance(meal_entries, list) else [],
	}

if __name__ == "__main__":
	# Fast local smoke test (no LLM / no Pinecone): only validates file save path + JSON write.
	test_recipe = {
		"name": "Smoke Test Recipe",
		"description": "Quick save test",
		"ingredients": [{"name": "oats", "quantity": 60, "unit": "g"}],
	}
	target_file = EXTRACTED_RECIPES_PATH

	print(f"[MealPlanner] Save-test target: {target_file}")
	add_recipe_to_json(test_recipe, target_file)
	print(f"[MealPlanner] Save-test complete. recipe_id={test_recipe.get('recipe_id')}")

