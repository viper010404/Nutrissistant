
import pandas as pd
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_DB_URL")
# SUPABASE_URL="postgresql://postgres:KA8n2f4CzDS07CaT@db.jotytkqymcmdenzrytfi.supabase.co:5432/postgres"

# if SUPABASE_URL and SUPABASE_URL.startswith("postgres://"):
#     SUPABASE_URL = SUPABASE_URL.replace("postgres://", "postgresql://", 1)

DATABASE_NOT_FOUND_MESSAGE = "SUPABASE_DB_URL not found. Cannot connect to database."
EMPTY_QUERY_RESULT_MESSAGE = "Connection successful, but no recipes that fit query were found."
SUCCESSFUL_QUERY_MESSAGE = "Success! Here are your recipes:\n"
ERROR_QUERY_MESSAGE = "Error querying Supabase: "

TABLE_NAMES =  ["recipes", "usda_foods"]

def query_database(query):
    if not SUPABASE_URL:
        return DATABASE_NOT_FOUND_MESSAGE, None
        
    engine = create_engine(SUPABASE_URL)
    
    try:
        df_result = pd.read_sql(query, engine)
        
        if df_result.empty:
            return EMPTY_QUERY_RESULT_MESSAGE, None
        else:
            return SUCCESSFUL_QUERY_MESSAGE, df_result
            
    except Exception as e:
        return ERROR_QUERY_MESSAGE + str(e), None


def parse_recipes_query_result(df_result):
    recipes = []
    for _, row in df_result.iterrows():
        recipe = {
            "name": row["name"],
            "ingredients": row["ingredients"],
            "instructions": row["instructions"],
            "steps": row["steps"],
            "calories": row["calories"],
            "minutes": row["minutes"]
        }
        recipes.append(recipe)
    return recipes

def parse_usda_foods_query_result(df_result):
    foods = []
    for _, row in df_result.iterrows():
        food = {
            "fdc_id": row["fdc_id"],
            "food_name": row["food_name"],
            "calories": row["calories"],
            "protein_g": row["protein_g"],
            "carbs_g": row["carbs_g"],
            "fat_g": row["fat_g"],
            "fiber_g": row["fiber_g"]
        }
        foods.append(food)
    return foods


def test_quick_recipes():
    """
    Connects to Supabase and prints recipes taking less than 20 minutes.
    """
    if not SUPABASE_URL:
        print("SUPABASE_DB_URL not found. Cannot connect to database.")
        return

    engine = create_engine(SUPABASE_URL)
    
    query = """
        SELECT name, minutes, ingredients 
        FROM recipes 
        WHERE minutes < 20 
        LIMIT 5;
    """
    
    try:
        df_quick = pd.read_sql(query, engine)
        
        if df_quick.empty:
            print("Connection successful, but no recipes under 20 minutes were found.")
        else:
            print("Success! Here are your quick recipes:\n")
            print(df_quick.to_string(index=False)) 
            
    except Exception as e:
        print(f"Error querying Supabase: {e}")

        
if __name__ == "__main__":
    test_quick_recipes()