
import pandas as pd
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

from src.config import (
    DB_NOT_FOUND_MESSAGE as DATABASE_NOT_FOUND_MESSAGE,
    DB_EMPTY_RESULT_MESSAGE as EMPTY_QUERY_RESULT_MESSAGE,
    DB_SUCCESS_MESSAGE as SUCCESSFUL_QUERY_MESSAGE,
    DB_ERROR_MESSAGE as ERROR_QUERY_MESSAGE,
)

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_DB_URL")
    

def query_database(query, params=None):
    if not SUPABASE_URL:
        return DATABASE_NOT_FOUND_MESSAGE, None

    engine = create_engine(SUPABASE_URL)

    try:
        df_result = pd.read_sql(text(query), engine, params=params)

        if df_result.empty:
            return EMPTY_QUERY_RESULT_MESSAGE, None
        return SUCCESSFUL_QUERY_MESSAGE, df_result

    except Exception as e:
        return ERROR_QUERY_MESSAGE + str(e), None

def get_ingridiants_dict(ingridiants, quantities):
    ingridiants_list = ingridiants.split(",")
    quantities_list = quantities.split(",")
    if len(ingridiants_list) != len(quantities_list):
        if len(ingridiants_list) > len(quantities_list):
            quantities_list += [""] * (len(ingridiants_list) - len(quantities_list))
        else:
            ingridiants_list += [""] * (len(quantities_list) - len(ingridiants_list))
    dic = [{"name": ingridiant, 
           "quantity": quantity}
            for ingridiant, quantity in zip(ingridiants_list, quantities_list)]
    return dic
    
    
def parse_recipes_query_result_full(df_result):
    recipes = [
        {
            "id": row["id"],
            "name": row["name"],
            "minutes": row["minutes"],
            "PrepTime": row.get("prep_time_mins"),
            "CookTime": row.get("cook_time_mins"),
            "category": row["category"],
            "tags": row["tags"],
            "description": row.get("description"),
            "ingredients": get_ingridiants_dict(row["ingredients"], row["ingredients_quantities"]),
            "instructions": row.get("instructions"),
            "nutritoin_info": {
                "calories": row["calories"],
                "fat_content": row["FatContent"],
                "saturated_fat_content": row["SaturatedFatContent"],
                "cholesterol_content": row["CholesterolContent"],
                "carbohydrate_content": row["CarbohydrateContent"],
                "sugar_content": row["SugarContent"],
                "fiber_content": row["FiberContent"],
                "protein_content": row["ProteinContent"],
            }}
        for _, row in df_result.iterrows()
    ]
    return recipes

def parse_recipes_query_result(df_result):
    
    recipes = []
    for _, row in df_result.iterrows():
        recipe = dict()
        for column in df_result.columns:
            recipe[column] = row[column]
        recipes.append(recipe)
    return recipes


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
    pass