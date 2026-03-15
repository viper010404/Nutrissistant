# ── data paths ──────────────────────────────────────────────────────────────
RECIPE_CSV_PATH = "data/recipes.csv"
RECIPE_FOR_VECTOR_DB_PATH = "data/recipes_for_vector_db.csv"

# ── category metadata ────────────────────────────────────────────────────────
CATAGORY_TYPES = {'meal_type': 10, 'dietary': 9, 'cuisine': 30, 'occasion': 7, 'cooking_method': 3, 'ingredient_based': 41}

# ── database ─────────────────────────────────────────────────────────────────
DB_RECIPES_TABLE = "recipes"
DB_TABLE_NAMES = [DB_RECIPES_TABLE]
DB_NOT_FOUND_MESSAGE = "SUPABASE_DB_URL not found. Cannot connect to database."
DB_EMPTY_RESULT_MESSAGE = "Connection successful, but no recipes that fit query were found."
DB_SUCCESS_MESSAGE = "Success! Here are your recipes:\n"
DB_ERROR_MESSAGE = "Error querying Supabase: "

# ── LLM ──────────────────────────────────────────────────────────────────────
LLM_MODEL_NAME = "RPRTHPB-gpt-5-mini"
RECIPE_EMBED_MODEL_DEFAULT = "RPRTHPB-text-embedding-3-small"

# ── agent ────────────────────────────────────────────────────────────────────
AGENT_MAX_ITERATIONS = 20