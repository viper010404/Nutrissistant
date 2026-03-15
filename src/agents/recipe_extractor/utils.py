from functools import lru_cache
import json
from src.utils.query_DB import parse_recipes_query_result, query_database
import os
from importlib import import_module

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from pinecone import Pinecone
from src.config import RECIPE_EMBED_MODEL_DEFAULT
from src.utils.LLM_utils import LLMOD_API_KEY, OPENAI_API_BASE

prompts_module = import_module("src.agents.recipe_extractor.prompts")
query_db_module = import_module("src.utils.query_DB")
llm_utils_module = import_module("src.utils.LLM_utils")


parse_recipes_query_result = query_db_module.parse_recipes_query_result
query_database = query_db_module.query_database


load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME_RECIPES = os.getenv("PINECONE_INDEX_NAME_RECIPES")
RECIPE_VECTOR_EMBED_MODEL = os.getenv("RECIPE_VECTOR_EMBED_MODEL", RECIPE_EMBED_MODEL_DEFAULT)


NUMERIC_RECIPE_FIELDS = [
    "minutes",
    "calories",
    "FatContent",
    "SaturatedFatContent",
    "CholesterolContent",
    "CarbohydrateContent",
    "SugarContent",
    "FiberContent",
    "ProteinContent",
]

RECIPE_KEY_ALIASES = {
    "id": ["id", "recipe_id", "recipeId"],
    "name": ["name", "title", "recipe_name"],
    "description": ["description", "desc", "summary"],
    "minutes": ["minutes", "total_time", "time_minutes", "prep_time_minutes", "total_time_mins"],
    "category": ["category", "meal_type", "course"],
    "ingredients": ["ingredients", "ingredient_list", "items"],
    "calories": ["calories", "kcal"],
    "FatContent": ["FatContent", "fat", "fat_content"],
    "SaturatedFatContent": ["SaturatedFatContent", "saturated_fat", "saturated_fat_content"],
    "CholesterolContent": ["CholesterolContent", "cholesterol", "cholesterol_content"],
    "CarbohydrateContent": ["CarbohydrateContent", "carbohydrates", "carbs", "carbohydrate_content"],
    "SugarContent": ["SugarContent", "sugar", "sugar_content"],
    "FiberContent": ["FiberContent", "fiber", "fiber_content"],
    "ProteinContent": ["ProteinContent", "protein", "protein_content"],
    "instructions": ["instructions", "steps", "directions"],
    "tags": ["tags"],
    "vector_score": ["vector_score", "score"],
}

DB_NUMERIC_COLUMN_SQL = {
    "calories": "calories",
    "fatcontent": '"FatContent"',
    "saturatedfatcontent": '"SaturatedFatContent"',
    "cholesterolcontent": '"CholesterolContent"',
    "carbohydratecontent": '"CarbohydrateContent"',
    "sugarcontent": '"SugarContent"',
    "fibercontent": '"FiberContent"',
    "proteincontent": '"ProteinContent"',
}


def _resolve_db_numeric_column(column_name: str | None):
    if not isinstance(column_name, str):
        return None
    return DB_NUMERIC_COLUMN_SQL.get(column_name.strip().lower())


def _normalize_recipe_id(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    # Common Pinecone/CSV artifact: numeric IDs serialized as "123.0".
    if text.endswith(".0"):
        maybe_int = text[:-2]
        if maybe_int.isdigit():
            return maybe_int

    return text


def _pick_first_value(source: dict, keys: list[str]):
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _to_float_or_none(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _to_int_or_none(value):
    as_float = _to_float_or_none(value)
    if as_float is None:
        return None
    return int(round(as_float))


def _normalize_ingredients(value):
    if value is None:
        return ""
    if isinstance(value, list):
        clean_items = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(clean_items)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        # Try to parse serialized JSON lists when present.
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    clean_items = [str(item).strip() for item in parsed if str(item).strip()]
                    return ", ".join(clean_items)
            except (ValueError, TypeError):
                pass
        return stripped
    return str(value).strip()


def normalize_recipe(recipe: dict | None) -> dict | None:
    """
    Normalize a recipe object into a canonical shape used across DB/vector/LLM sources.
    """
    if not isinstance(recipe, dict):
        return None

    normalized = dict(recipe)

    for canonical_key, aliases in RECIPE_KEY_ALIASES.items():
        picked = _pick_first_value(recipe, aliases)
        if picked is not None:
            normalized[canonical_key] = picked

    if normalized.get("id") is not None:
        normalized["id"] = str(normalized["id"])

    if normalized.get("name") is not None:
        normalized["name"] = str(normalized["name"]).strip()

    if normalized.get("description") is not None:
        normalized["description"] = str(normalized["description"]).strip()

    normalized["minutes"] = _to_int_or_none(normalized.get("minutes"))

    for numeric_key in NUMERIC_RECIPE_FIELDS:
        if numeric_key == "minutes":
            continue
        normalized[numeric_key] = _to_float_or_none(normalized.get(numeric_key))

    normalized["ingredients"] = _normalize_ingredients(normalized.get("ingredients"))

    tags_value = normalized.get("tags")
    if isinstance(tags_value, str):
        normalized["tags"] = [tag.strip() for tag in tags_value.split(",") if tag.strip()]
    elif isinstance(tags_value, list):
        normalized["tags"] = [str(tag).strip() for tag in tags_value if str(tag).strip()]

    normalized["vector_score"] = _to_float_or_none(normalized.get("vector_score"))

    return normalized


def normalize_recipe_list(recipes) -> list:
    if not isinstance(recipes, list):
        return []
    normalized = []
    for recipe in recipes:
        recipe_normalized = normalize_recipe(recipe)
        if recipe_normalized is not None:
            normalized.append(recipe_normalized)
    return normalized


# stractured query db help functions

def get_catagory_from_context(context, step_tracer=None):
    result = llm_utils_module._invoke_json_llm(prompts_module.GET_CATEGORY_SYSTEM_PROMPT, context, step_tracer)
    return result.get("category")

def get_recipe_name_suggestions(context, step_tracer=None):
    result = llm_utils_module._invoke_json_llm(prompts_module.GET_RECIPE_NAME_SUGGESTION_SYSTEM_PROMPT, context, step_tracer)
    if not isinstance(result, dict):
        return None

    recipe_names = result.get("recipe_names", [])
    if not isinstance(recipe_names, list):
        return None

    clean_names = [name for name in recipe_names if isinstance(name, str) and name.strip()]
    return clean_names[:5]

def add_excluded_ingridients(diatery_restrictions, allergies):
    # call llm to add excluded ingridients based on diatery_restrictions and allergies
    system_prompt = prompts_module.ADD_EXCLUDED_INGRIDIENTS_SYSTEM_PROMPT
    context = {
        "dietary_restrictions": diatery_restrictions,
        "allergies": allergies
    }
    result = llm_utils_module._invoke_json_llm(system_prompt, context)
    excluded_ingredients = result.get("excluded_ingredients", [])
    return excluded_ingredients

# only call at the end - before returning
def get_recipe_instructions(recipe_id):
    query = """
    SELECT instructions
    FROM recipes
    WHERE id = :recipe_id
    """
    message, df = query_database(query, {"recipe_id": recipe_id})
    if df is not None and not df.empty:
        return df.iloc[0]["instructions"]
    return message

# is this a tool? or something we use in the ui when presenting it and stuff
def get_recipe_details_small(recipe_id) -> dict:
    query = """
    SELECT id, name, minutes, ingredients, category, calories, "FatContent", "SaturatedFatContent",
        "CholesterolContent", "CarbohydrateContent", "SugarContent", "FiberContent", "ProteinContent"
    FROM recipes
    WHERE id = :recipe_id
    """
    message, df = query_database(query, {"recipe_id": recipe_id})
    if df is not None and not df.empty:
        details = normalize_recipe(parse_recipes_query_result(df)[0])
        return message, details
    return message, None

def context_to_vector_query_return(llm_response):
    # Validate and parse the LLM response to extract the vector query parameters
    if not isinstance(llm_response, dict):
        return None
    if llm_response.get("status") != "ok":
        return None

    query_text = None
    top_k = 5

    # New expected shape: {"status": "ok", "vector_query": {...}}
    payload = llm_response.get("vector_query")
    if isinstance(payload, dict):
        query_text = payload.get("query_text")
        top_k = payload.get("top_k", 5)
    else:
        # Backward-compatible shape
        query_text = llm_response.get("query_text")
        top_k = llm_response.get("top_k", 5)

    if not query_text:
        return None

    try:
        top_k = max(1, (int(top_k)))
    except (TypeError, ValueError):
        top_k = 5

    return {"query_text": str(query_text).strip(), "top_k": top_k}


@lru_cache(maxsize=1)
def _get_recipe_embeddings_client():
    if not LLMOD_API_KEY:
        raise ValueError("Missing embedding API key (LLMOD_API_KEY).")

    kwargs = {
        "model": RECIPE_VECTOR_EMBED_MODEL,
        "api_key": LLMOD_API_KEY,
    }
    if OPENAI_API_BASE:
        kwargs["base_url"] = OPENAI_API_BASE

    return OpenAIEmbeddings(**kwargs)


@lru_cache(maxsize=1)
def _get_recipe_pinecone_index():
    if not PINECONE_API_KEY or not PINECONE_INDEX_NAME_RECIPES:
        raise ValueError("Missing Pinecone configuration in environment variables.")

    pc = Pinecone(api_key=PINECONE_API_KEY)
    return pc.Index(PINECONE_INDEX_NAME_RECIPES)


def get_recipes_by_rows_id(ordered_ids):
    if not ordered_ids:
        return []
    

    recipes = []
    for rid in ordered_ids:
        rid = _normalize_recipe_id(rid)
        if not rid:
            continue
        message, result = get_recipe_details_small(rid)
        if isinstance(result, dict):
            recipes.append(result)
    
    return recipes

def stractured_query(
    names: list | None = None,
    category: str | None = None,
    max_total_time: int | None = None,
    nutrition_constraints_min: dict | None = None,
    nutrition_constraints_max: dict | None = None,
    available_ingredients: list | None = None,
    must_have_all_ingredients: bool = False,
    exclude_ingredients: list | None = None,
    tags : list | None = None,
    limit: int = 10,
):
    """
    Function to query the recipe database using structured parameters.
    """
    query = """
    SELECT id, name, description, minutes, calories, category, "FatContent", "SaturatedFatContent", "CholesterolContent", "CarbohydrateContent", "SugarContent", "FiberContent", "ProteinContent", ingredients
    FROM recipes
    WHERE 1=1
    """
    if names:
        # Extract individual keywords from name suggestions rather than matching full phrases.
        # Full-phrase ILIKE patterns (e.g. '%Chicken Rice Bowl%') almost never match real recipe
        # names, causing zero results.  Individual keywords (e.g. '%chicken%' OR '%rice%') are
        # far more permissive while still steering the query toward relevant recipes.
        _STOP_WORDS = {
            "and", "or", "the", "a", "an", "with", "in", "on", "for", "of", "to", "from",
            "simple", "easy", "quick", "homemade", "classic", "style", "recipe", "how",
            "make", "made", "best", "great", "good", "my", "your",
        }
        keywords: set[str] = set()
        for name in names:
            for word in str(name).split():
                word_clean = word.strip(".,()-/&").strip().lower()
                if len(word_clean) > 2 and word_clean not in _STOP_WORDS:
                    keywords.add(word_clean)
        if keywords:
            name_conditions = [f"name ILIKE '%{kw}%'" for kw in sorted(keywords)]
            query += " AND (" + " OR ".join(name_conditions) + ")"
    if category:
        query += f" AND category = '{category}'"
    if max_total_time:
        query += f" AND minutes <= {max_total_time}"
    if nutrition_constraints_min or nutrition_constraints_max:
        all_nutrients: set[str] = set()
        if nutrition_constraints_min:
            all_nutrients.update(nutrition_constraints_min.keys())
        if nutrition_constraints_max:
            all_nutrients.update(nutrition_constraints_max.keys())
        for nutrient in all_nutrients:
            sql_col = _resolve_db_numeric_column(nutrient)
            if sql_col is None:
                continue
            min_val = nutrition_constraints_min.get(nutrient) if nutrition_constraints_min else None
            max_val = nutrition_constraints_max.get(nutrient) if nutrition_constraints_max else None
            if min_val is not None and max_val is not None:
                query += f" AND {sql_col} BETWEEN {min_val} AND {max_val}"
            elif min_val is not None:
                query += f" AND {sql_col} >= {min_val}"
            elif max_val is not None:
                query += f" AND {sql_col} <= {max_val}"
    if available_ingredients:
        ingredient_conditions = []
        for ingredient in available_ingredients:
            ingredient_conditions.append(f"ingredients ILIKE '%{ingredient}%'")
        if must_have_all_ingredients:
            query += " AND " + " AND ".join(ingredient_conditions)
        else:
            query += " AND (" + " OR ".join(ingredient_conditions) + ")"

    if exclude_ingredients:
        for ingredient in exclude_ingredients:
            query += f" AND ingredients NOT ILIKE '%{ingredient}%'"

    if tags:
        tag_conditions = []
        for tag in tags:
            tag_conditions.append(f"tags ILIKE '%{tag}%'")
        query += " AND (" + " OR ".join(tag_conditions) + ")"

    query += f" LIMIT {limit}"

    message, df = query_database(query)
    if df is not None and not df.empty:
        recipes = normalize_recipe_list(parse_recipes_query_result(df))
        return {"status": "success", "source": "database", "recipes": recipes}
    return {"status": "failed", "source": "database", "message": message}



def query_vector_db(vector_query):
    if not vector_query:
        return None
    query_text = vector_query.get("query_text")
    top_k = vector_query.get("top_k", 5)
    if not query_text:
        return None

    embeddings = _get_recipe_embeddings_client()
    index = _get_recipe_pinecone_index()

    q_vec = embeddings.embed_query(query_text)
    result = index.query(vector=q_vec, top_k=top_k, include_metadata=True)
    matches = result.get("matches", []) if isinstance(result, dict) else getattr(result, "matches", [])

    best_scores_by_id = {}

    for match in matches:
        if isinstance(match, dict):
            rid = _normalize_recipe_id(match.get("id", ""))
            score = float(match.get("score", 0.0))
            metadata = match.get("metadata") if isinstance(match.get("metadata"), dict) else {}
        else:
            rid = _normalize_recipe_id(getattr(match, "id", ""))
            score = float(getattr(match, "score", 0.0))
            metadata = getattr(match, "metadata", None)
            if not isinstance(metadata, dict):
                metadata = {}

        # Fallback when vector id is not directly usable for DB lookup.
        if not rid:
            rid = _normalize_recipe_id(metadata.get("id") or metadata.get("recipe_id") or metadata.get("recipeId"))

        if not rid:
            continue
        best_scores_by_id[rid] = max(score, best_scores_by_id.get(rid, 0.0))

    if not best_scores_by_id:
        return []

    ordered_ids = [rid for rid, _ in sorted(best_scores_by_id.items(), key=lambda x: x[1], reverse=True)[:top_k]]
    recipes = get_recipes_by_rows_id(ordered_ids)

    # attach vector score for downstream reranking/debugging
    for recipe in recipes:
        rid = str(recipe.get("id", ""))
        recipe["vector_score"] = best_scores_by_id.get(rid, 0.0)
    return normalize_recipe_list(recipes)
    

# validation

def validate_query_params(
    names=None,
    category=None,
    max_total_time=None,
    nutrition_constraints_min=None,
    nutrition_constraints_max=None,
    available_ingredients=None,
    must_have_all_ingredients=False,
    exclude_ingredients=None,
    tags=None,
    limit=10,
):
    errors = []

    if names is not None and not isinstance(names, list):
        errors.append("names must be a list or None.")
    if category is not None and not isinstance(category, str):
        errors.append("category must be a string or None.")
    if max_total_time is not None and (not isinstance(max_total_time, int) or max_total_time < 0):
        errors.append("max_total_time must be a non-negative integer or None.")
    if nutrition_constraints_min is not None and not isinstance(nutrition_constraints_min, dict):
        errors.append("nutrition_constraints_min must be a dict or None.")
    if nutrition_constraints_max is not None and not isinstance(nutrition_constraints_max, dict):
        errors.append("nutrition_constraints_max must be a dict or None.")
    if available_ingredients is not None and not isinstance(available_ingredients, list):
        errors.append("available_ingredients must be a list or None.")
    if not isinstance(must_have_all_ingredients, bool):
        errors.append("must_have_all_ingredients must be a boolean.")
    if exclude_ingredients is not None and not isinstance(exclude_ingredients, list):
        errors.append("exclude_ingredients must be a list or None.")
    if tags is not None and not isinstance(tags, list):
        errors.append("tags must be a list or None.")
    if not isinstance(limit, int) or limit <= 0:
        errors.append("limit must be a positive integer.")

    return errors
