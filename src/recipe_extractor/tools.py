import functools
import json
from src.utils.query_DB import query_database, parse_recipes_query_result_full
import os
from importlib import import_module

from dotenv import load_dotenv
from langchain_core.tools import Tool

_MODULE_NAME = "RecipeExtractor"


prompts_module = import_module("src.recipe_extractor.prompts")
query_db_module = import_module("src.utils.query_DB")
llm_utils_module = import_module("src.utils.LLM_utils")
utils_module = import_module("src.recipe_extractor.utils")

parse_recipes_query_result = query_db_module.parse_recipes_query_result
query_database = query_db_module.query_database

load_dotenv()


def _looks_like_recipe_context(payload):
    if not isinstance(payload, dict):
        return False
    expected_keys = {
        "request_type",
        "meal_context",
        "constraints",
        "nutritional_targets_min",
        "nutritional_targets_max",
        "revision_context",
        "user_preferences",
    }
    return any(key in payload for key in expected_keys)


def _extract_dict_from_arg(tool_input):
    if not isinstance(tool_input, dict):
        return None

    raw_arg = tool_input.get("__arg1")
    if isinstance(raw_arg, dict):
        return raw_arg

    if isinstance(raw_arg, str):
        extracted = _extract_dict_from_text(raw_arg)
        if isinstance(extracted, dict):
            return extracted

    return None


def _extract_balanced_json_object(raw_text: str):
    if not isinstance(raw_text, str):
        return None

    start = raw_text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(raw_text)):
        ch = raw_text[index]

        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw_text[start : index + 1]

    return None


def _extract_dict_from_text(raw_text: str):
    if not isinstance(raw_text, str):
        return None

    stripped = raw_text.strip()
    if not stripped:
        return None

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass

    candidate = _extract_balanced_json_object(stripped)
    if not candidate:
        return None

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        return None

    return None


def _resolve_context(tool_input, base_context=None):
    if isinstance(tool_input, dict):
        if _looks_like_recipe_context(tool_input):
            return tool_input

        nested = tool_input.get("context")
        if _looks_like_recipe_context(nested):
            return nested

        arg_payload = _extract_dict_from_arg(tool_input)
        if _looks_like_recipe_context(arg_payload):
            return arg_payload

    if isinstance(base_context, dict):
        return base_context

    if isinstance(tool_input, dict):
        return tool_input

    return {}


def _run_generation_tool(tool_input, tool_func, base_context=None, step_tracer=None):
    resolved_context = _resolve_context(tool_input, base_context)
    return tool_func(resolved_context, step_tracer=step_tracer)


def _parse_tool_payload(tool_input):
    if isinstance(tool_input, dict):
        arg_payload = _extract_dict_from_arg(tool_input)
        if isinstance(arg_payload, dict):
            return arg_payload

        raw_arg = tool_input.get("__arg1") if isinstance(tool_input.get("__arg1"), str) else None
        parsed_from_text = _extract_dict_from_text(raw_arg) if raw_arg else None
        if isinstance(parsed_from_text, dict):
            return parsed_from_text

        return tool_input
    if isinstance(tool_input, str):
        stripped = tool_input.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            return {"value": stripped}
    return {}


def _tool_input_error(message, details=None, source="tool_input_validation"):
    payload = {
        "status": "failed",
        "source": source,
        "message": message,
    }
    if details is not None:
        payload["details"] = details
    return payload


def _missing_recipe_guidance(source: str):
    return {
        "status": "blocked",
        "source": source,
        "message": "No recipe payload provided. Do not call evaluator tools before retrieval/generation.",
        "details": {
            "expected": ["recipe", "candidate_recipe", "original_recipe"],
            "next_step": "Call one generation/retrieval tool first.",
            "recommended_tools": [
                "STRACTURED_DATABASE_QUERY",
                "FREE_QUERY_DATABASE",
                "USE_VECTOR_DB",
                "LLM_GENERATION",
            ],
        },
    }


def _extract_recipe_from_payload(payload, fallback_key="recipe"):
    if not isinstance(payload, dict):
        return None

    for key in (fallback_key, "candidate_recipe", "original_recipe"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    if any(key in payload for key in ("id", "recipe_id", "name", "ingredients", "minutes", "calories", "nutrition_per_serving")):
        return payload

    value = payload.get("value")
    if isinstance(value, dict):
        return value

    return None


def _run_evaluation_tool(tool_input, tool_func, base_context=None, step_tracer=None):
    payload = _parse_tool_payload(tool_input)
    resolved_context = _resolve_context(payload.get("context"), base_context)
    recipe = _extract_recipe_from_payload(payload, fallback_key="recipe")
    if recipe is None:
        return _missing_recipe_guidance("llm_evaluation")
    return tool_func(resolved_context, recipe, step_tracer=step_tracer)


def _run_strict_evaluation_tool(tool_input, base_context=None):
    payload = _parse_tool_payload(tool_input)
    resolved_context = _resolve_context(payload.get("context"), base_context)
    recipe = _extract_recipe_from_payload(payload, fallback_key="recipe")
    if recipe is None:
        return _missing_recipe_guidance("strict_evaluation")
    return strict_evaluate_recipe(resolved_context, recipe)


def _run_refinement_tool(tool_input, tool_func, base_context=None, step_tracer=None):
    payload = _parse_tool_payload(tool_input)
    resolved_context = _resolve_context(payload.get("context"), base_context)
    recipe = _extract_recipe_from_payload(payload, fallback_key="original_recipe")
    if recipe is None:
        return _tool_input_error(
            "Revision tool expected an original recipe payload but none was provided.",
            details={"expected": ["original_recipe", "recipe", "candidate_recipe"]},
            source="llm_revision",
        )
    return tool_func(resolved_context, recipe, step_tracer=step_tracer)

def context_to_query_DB_llm(context, step_tracer=None):
    """
    Tool to query the recipe database using the LLM system prompt for context-to-query conversion.
    Uses the structured_query function with parameters extracted from context.
    """
    # Use the system prompt to convert context to query parameters
    prompt = prompts_module.CONTEXT_TO_QUERY_DB_SYSTEM_PROMPT
    result = llm_utils_module._invoke_json_llm(prompt, context, step_tracer, module_name=_MODULE_NAME)
    if not isinstance(result, dict) or result.get("status") != "ok":
        return {
            "status": "failed",
            "source": "llm_context_to_query",
            "message": "LLM failed to convert context to query parameters.",
        }
    query_params = result.get("query_params", {})
    if not isinstance(query_params, dict):
        query_params = {}

    # Normalize key aliases coming from prompt/LLM output.
    if "exclude_ingredients" not in query_params and "excluded_ingredients" in query_params:
        query_params["exclude_ingredients"] = query_params.get("excluded_ingredients")
    query_params.pop("excluded_ingredients", None)

    # TODO: make sure the returned value a json dict?
    errors = utils_module.validate_query_params(**query_params)
    if len(errors) > 0:
        return {
            "status": "failed",
            "source": "llm",
            "message": str(errors),
        }   

    # Call the structured query function with the parameters
    result = utils_module.stractured_query(
        names=query_params.get("names"),
        category=query_params.get("category"),
        max_total_time=query_params.get("max_total_time"),
        nutrition_constraints_min=query_params.get("nutrition_constraints_min"),
        nutrition_constraints_max=query_params.get("nutrition_constraints_max"),
        available_ingredients=query_params.get("available_ingredients"),
        must_have_all_ingredients=query_params.get("must_have_all_ingredients", False),
        exclude_ingredients=query_params.get("exclude_ingredients"),
        tags=query_params.get("tags"),
        limit=query_params.get("limit", 10)
    )

    if isinstance(result, dict) and result.get("status") == "success":
        result["recipes"] = utils_module.normalize_recipe_list(result.get("recipes", []))

    return result
    

def get_recipe_from_database_stractured(context, step_tracer=None):
    names = utils_module.get_recipe_name_suggestions(context, step_tracer)
    category = utils_module.get_catagory_from_context(context, step_tracer)
    max_total_time = context.get("meal_context", {}).get("free_time_mins")
    available_ingredients = context.get("constraints", {}).get("available_ingredients", None)
    must_have_all_ingredients = context.get("constraints", {}).get("must_have_all_ingredients", False)
    nutrition_constraints_min = context.get("nutritional_targets_min", None)
    nutrition_constraints_max = context.get("nutritional_targets_max", None)
    exclude_ingredients = context.get("constraints", {}).get("excluded_ingredients", [])

    if context.get("constraints", {}).get("dietary_restrictions") or context.get("constraints", {}).get("allergies"):
        diatery_restrictions = context.get("constraints", {}).get("dietary_restrictions", [])
        allergies = context.get("constraints", {}).get("allergies", [])
        exclude_ingredients = exclude_ingredients + utils_module.add_excluded_ingridients(diatery_restrictions, allergies)

    limit = context.get("limit", 10)
    result = utils_module.stractured_query(
        names=names,
        category=category,
        max_total_time=max_total_time,
        nutrition_constraints_min=nutrition_constraints_min,
        nutrition_constraints_max=nutrition_constraints_max,
        available_ingredients=available_ingredients,
        must_have_all_ingredients=must_have_all_ingredients,
        exclude_ingredients=exclude_ingredients,
        limit=limit
    )

    if isinstance(result, dict) and result.get("status") == "success":
        result["recipes"] = utils_module.normalize_recipe_list(result.get("recipes", []))

    return result


def generate_recepie_with_llm(context, step_tracer=None):
    """
    Function to generate a new recipe using the LLM based on user context and requirements.
    """
    result = llm_utils_module._invoke_json_llm(prompts_module.GENERATE_RECEPIE_SYSTEM_PROMPT, context, step_tracer, module_name=_MODULE_NAME)
    if isinstance(result, dict):
        if isinstance(result.get("recipe"), dict):
            normalized_recipe = utils_module.normalize_recipe(result.get("recipe"))
            if normalized_recipe is not None:
                result["recipe"] = normalized_recipe
        elif result.get("status") == "ok":
            normalized_recipe = utils_module.normalize_recipe(result)
            if normalized_recipe is not None:
                return normalized_recipe
    return result


def query_vector_database(context, step_tracer=None):
    """
    Function to query a vector database of recipes based on the user's context.
    Uses the LLM to convert the context into a vector query format, then queries the vector DB.
    """ 
    prompt = prompts_module.QUERY_VECTOR_DB_SYSTEM_PROMPT
    response = llm_utils_module._invoke_json_llm(prompt, context, step_tracer, module_name=_MODULE_NAME)
    vector_query = utils_module.context_to_vector_query_return(response)
    vector_results = utils_module.normalize_recipe_list(utils_module.query_vector_db(vector_query))
    if vector_results:
        return {"status": "success", "source": "vector_db", "recipes": vector_results}
    return {"status": "failed", "source": "vector_db", "message": "No matching recipes found in vector database."}


def build_generation_tools(step_tracer=None, base_context=None):
    return [
        Tool(
            name="FREE_QUERY_DATABASE",
            func=functools.partial(
                _run_generation_tool,
                tool_func=context_to_query_DB_llm,
                base_context=base_context,
                step_tracer=step_tracer,
            ),
            description="Best for natural-language requests. Converts full context into structured DB filters with an LLM, then runs SQL retrieval. Returns candidate recipes from the recipes table when constraints are somewhat fuzzy or text-heavy.",
        ),
        Tool(
            name="STRACTURED_DATABASE_QUERY",
            func=functools.partial(
                _run_generation_tool,
                tool_func=get_recipe_from_database_stractured,
                base_context=base_context,
                step_tracer=step_tracer,
            ),
            description="Best for explicit constraints. Builds deterministic filters (time, category, nutrition bounds, available/excluded ingredients) and retrieves matching DB recipes. Use when user requirements are clear and structured.",
        ),
        Tool(
            name="LLM_GENERATION",
            func=functools.partial(
                _run_generation_tool,
                tool_func=generate_recepie_with_llm,
                base_context=base_context,
                step_tracer=step_tracer,
            ),
            description="Generates one new recipe JSON from context (including ingredients, instructions, tags, timing, and nutrition-per-serving). Use when DB/vector retrieval cannot satisfy the request or when customization is high.",
        ),
        Tool(
            name="USE_VECTOR_DB",
            func=functools.partial(
                _run_generation_tool,
                tool_func=query_vector_database,
                base_context=base_context,
                step_tracer=step_tracer,
            ),
            description="Semantic retrieval over recipe embeddings. Converts context to a vector query and returns nearest recipe candidates. Use for fuzzy intent (style/flavor similarity) or when exact DB filters miss good matches.",
        ),
    ]

generation_tools = build_generation_tools()

def evaluate_recipe_llm(context, recipe, step_tracer=None):
    """
    Function to evaluate a recipe using the LLM based on user context and requirements.
    The LLM can provide feedback on how well the recipe meets the user's goals and suggest improvements.
    """
    prompt = prompts_module.EVALUATE_RECEPIE_LLM_SYSTEM_PROMPT
    evaluation_context = {
        "recipe": recipe,
        "constraints": context.get("constraints", {}),
        "nutritional_targets_min": context.get("nutritional_targets_min", {}),
        "nutritional_targets_max": context.get("nutritional_targets_max", {}),
        "meal_context": context.get("meal_context", {}),
    }
    evaluation_result = llm_utils_module._invoke_json_llm(prompt, evaluation_context, step_tracer, module_name=_MODULE_NAME)
    if not isinstance(evaluation_result, dict) or evaluation_result.get("status") != "ok":
        return {
            "status": "failed",
            "source": "llm_evaluation",
            "message": "LLM evaluation failed or returned invalid result.",
        }
    return evaluation_result

def strict_evaluate_recipe(context, recipe):
    """
    A strict evaluation function that checks if a recipe meets the user's constraints and nutritional targets.
    This can be a rule-based function that returns a pass/fail or a list of issues with the recipe.
    """
    recipe = utils_module.normalize_recipe(recipe)
    if recipe is None:
        return {
            "status": "failed",
            "source": "strict_evaluation",
            "message": "Invalid recipe payload.",
        }

    # check min and max nutritional targets
    min_targets = context.get("nutritional_targets_min", {})
    max_targets = context.get("nutritional_targets_max", {})

    nutrition_per_serving = recipe.get("nutrition_per_serving")
    if not isinstance(nutrition_per_serving, dict):
        nutrition_per_serving = {}

    nutrition_key_map = {
        "calories": "calories",
        "ProteinContent": "protein_g",
        "CarbohydrateContent": "carbs_g",
        "FatContent": "fat_g",
        "FiberContent": "fiber_g",
    }

    def _get_nutrient_value(rec, nutrient_key):
        direct = rec.get(nutrient_key)
        if isinstance(direct, (int, float)):
            return float(direct)

        mapped_key = nutrition_key_map.get(nutrient_key)
        if mapped_key is None:
            return None

        mapped_value = nutrition_per_serving.get(mapped_key)
        if isinstance(mapped_value, (int, float)):
            return float(mapped_value)

        return None

    for nutrient, min_value in min_targets.items():
        nutrient_value = _get_nutrient_value(recipe, nutrient)
        if nutrient_value is not None and nutrient_value < min_value:
            return {
                "status": "failed",
                "source": "strict_evaluation",
                "message": f"Recipe does not meet minimum target for {nutrient}.",
            }
    for nutrient, max_value in max_targets.items():
        nutrient_value = _get_nutrient_value(recipe, nutrient)
        if nutrient_value is not None and nutrient_value > max_value:
            return {
                "status": "failed",
                "source": "strict_evaluation",
                "message": f"Recipe exceeds maximum target for {nutrient}.",
            }
    
    # check if recipe contains any excluded ingredients
    exclude_ingredients = context.get("constraints", {}).get("excluded_ingredients", [])
    recipe_ingredients = recipe.get("ingredients", "")    
    for ingredient in exclude_ingredients:
        if ingredient.lower() in recipe_ingredients.lower():
            return {
                "status": "failed",
                "source": "strict_evaluation",
                "message": f"Recipe contains excluded ingredient: {ingredient}.",
            }
    # check if recipe meets the time constraint
    minutes = recipe.get("minutes")
    max_total_time = context.get("meal_context", {}).get("free_time_mins")
    if minutes is not None and max_total_time is not None and minutes > max_total_time:
        return {
            "status": "failed",
            "source": "strict_evaluation",
            "message": f"Recipe takes {minutes} minutes which exceeds the time constraint of {max_total_time} minutes.",
        }
    
    return {
        "status": "success",
        "source": "strict_evaluation",
        "message": "Recipe meets all constraints and nutritional targets.",
    }


def build_evaluation_tools(step_tracer=None, base_context=None):
    return [
        Tool(
            name="LLM_EVALUATOR",
            func=functools.partial(
                _run_evaluation_tool,
                tool_func=evaluate_recipe_llm,
                base_context=base_context,
                step_tracer=step_tracer,
            ),
            description="Soft scoring and ranking tool. Evaluates one candidate recipe against context and returns overall score, dimension scores, strengths/issues, and improvement suggestions. Use after strict filtering to pick the best recipe.",
        ),
        Tool(
            name="STRICT_EVALUATOR",
            func=functools.partial(
                _run_strict_evaluation_tool,
                base_context=base_context,
            ),
            description="Hard rule-based validator. Fails recipes that violate excluded ingredients, nutrition min/max bounds, or available time. Use as mandatory pass/fail gate before LLM_EVALUATOR ranking.",
        ),
    ]

evaluation_tools = build_evaluation_tools()

def revise_recepie_llm(context, recipe, step_tracer=None):
    """
    Function to revise a recipe using the LLM based on user feedback and evaluation results.
    The LLM can suggest modifications to the recipe to better align it with the user's goals, such as adjusting ingredients, cooking methods, or portion sizes.
    """
    prompt = prompts_module.REVISE_RECEPIE_LLM_SYSTEM_PROMPT
    revision_context = {
        "original_recipe": recipe,
        "feedback": context.get("revision_context", {}).get("feedback", ""),
        "keep_base_recipe": context.get("revision_context", {}).get("keep_base_recipe", False),
        "constraints": context.get("constraints", {}),
        "nutritional_targets_min": context.get("nutritional_targets_min", {}),
        "nutritional_targets_max": context.get("nutritional_targets_max", {}),
        "meal_context": context.get("meal_context", {}),
    }
    revised_recipe = llm_utils_module._invoke_json_llm(prompt, revision_context, step_tracer, module_name=_MODULE_NAME)
    if not isinstance(revised_recipe, dict) or revised_recipe.get("status") != "ok":
        return {
            "status": "failed",
            "source": "llm_revision",
            "message": "LLM revision failed or returned invalid result.",
        }
    return revised_recipe

def build_refinement_tools(step_tracer=None, base_context=None):
    return [
        Tool(
            name="LLM_REVISER",
            func=functools.partial(
                _run_refinement_tool,
                tool_func=revise_recepie_llm,
                base_context=base_context,
                step_tracer=step_tracer,
            ),
            description="Refines an existing recipe using revision feedback while preserving base identity when requested. Use to fix evaluator issues (time, constraints, nutrition, ingredient choices) without restarting generation.",
        ),
    ]

refinement_tools = build_refinement_tools()

def get_recipe_details_full(recipe_id: str):
    """
    Function to get the full details of a recipe given its ID.
    Used at the very end to output the final recipe details to the user. This is a simple DB query without LLM involvement, since we already have the recipe ID at this point.
    """
    resolved_recipe_id = str(recipe_id).strip() if recipe_id is not None else ""
    if resolved_recipe_id.lower().startswith("db_"):
        resolved_recipe_id = resolved_recipe_id[3:]

    normalize_id = getattr(utils_module, "_normalize_recipe_id", None)
    if callable(normalize_id):
        resolved_recipe_id = normalize_id(resolved_recipe_id)

    if not resolved_recipe_id:
        return {
            "status": "failed",
            "source": "database",
            "message": "Invalid recipe_id provided.",
        }

    query = """
    SELECT *
    FROM recipes
    WHERE id = :recipe_id
    """
    message, df = query_database(query, {"recipe_id": resolved_recipe_id})
    if df is not None:
        recipe_details = parse_recipes_query_result_full(df)
        if not recipe_details:
            return {
                "status": "failed",
                "source": "database",
                "message": f"No recipe found with ID {recipe_id}.",
            }

        final_recipe = recipe_details[0]

        # Ensure instructions are present in final output. If absent in parsed full-row data,
        # fetch them explicitly from DB using the instructions helper.
        instructions = final_recipe.get("instructions")
        if instructions is None or (isinstance(instructions, str) and not instructions.strip()):
            fetched_instructions = utils_module.get_recipe_instructions(resolved_recipe_id)
            if fetched_instructions is not None:
                final_recipe["instructions"] = fetched_instructions

        return final_recipe
    return message

def build_output_tools():
    return [
        Tool(
            name="GET_FULL_RECEPIE_DETAILS",
            func=get_recipe_details_full,
            description="Finalization tool for DB-selected recipes. Fetches full recipe row by recipe_id (including description and instructions fallback) to produce the final user-facing recipe payload.",
        ),
    ]

output_tools = build_output_tools()

