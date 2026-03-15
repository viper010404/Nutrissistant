import pandas as pd
from sqlalchemy import create_engine
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from constants import RECIPE_CSV_PATH
# Load environment variables
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_DB_URL")

if SUPABASE_URL and SUPABASE_URL.startswith("postgres://"):
    SUPABASE_URL = SUPABASE_URL.replace("postgres://", "postgresql://", 1)

def clean_r_vector_string(text):
    """
    Cleans R-style string vectors like: c(""sugar"", ""lemons"")
    Returns clean text: sugar, lemons
    """
    if pd.isna(text):
        return text
        
    text = str(text).strip()
    # Remove the surrounding c(...)
    if text.startswith('c(') and text.endswith(')'):
        text = text[2:-1]
        
    # Remove all the double and single quotes
    text = text.replace('"', '').replace("'", "")
    return text.strip()

def build_recipe_database():
    # Load the raw dataset
    print("Loading recipes.csv...")
    
    # Use the exact column names from CSV
    target_columns = [
        'RecipeId', 'Name', 'TotalTime', 'Keywords', 
        'PrepTime', 'CookTime', 'Description',
        'RecipeIngredientParts', 'RecipeIngredientQuantities',
        'RecipeInstructions', 'Calories', 'RecipeCategory',
        'FatContent', 'SaturatedFatContent', 'CholesterolContent','CarbohydrateContent', 'SugarContent', 'FiberContent', 'ProteinContent'
    ]
    
    df = pd.read_csv(RECIPE_CSV_PATH, usecols=target_columns)

    # Rename columns to be lowercased and SQL-friendly
    df = df.rename(columns={
        'RecipeId': "id",
        'Name': 'name',
        'TotalTime': 'total_time',
        'Keywords': 'tags',
        'RecipeIngredientParts': 'ingredients',
        'RecipeInstructions': 'instructions',
        'RecipeIngredientQuantities': 'ingredients_quantities',
        'Calories': 'calories',
        'RecipeCategory': 'category',
        'Description': 'description',
    })

    # Drop rows with missing critical data
    df = df.dropna(subset=['name', 'total_time', 'ingredients', 'instructions'])

    print("Converting ISO 8601 durations to integer minutes...")
    
    # Extract hours and minutes into separate columns using regex
    # It looks for "PT", then optionally extracts digits before "H", and optionally digits before "M"
    extracted_time = df['total_time'].str.extract(r'PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?')
    
    # Fill missing values with 0 and convert to floats for calculation
    extracted_time = extracted_time.fillna(0).astype(float)
    
    # Calculate total minutes
    df['minutes'] = (extracted_time['hours'] * 60) + extracted_time['minutes']
    
    # Convert back to an integer
    df['minutes'] = df['minutes'].astype(int)

    extracted_prep_time = df['PrepTime'].str.extract(r'PT(?:(?P<prep_hours>\d+)H)?(?:(?P<prep_minutes>\d+)M)?')
    extracted_cook_time = df['CookTime'].str.extract(r'PT(?:(?P<cook_hours>\d+)H)?(?:(?P<cook_minutes>\d+)M)?')
    
    extracted_prep_time = extracted_prep_time.fillna(0).astype(float)
    extracted_cook_time = extracted_cook_time.fillna(0).astype(float)

    df['prep_time_mins'] = (extracted_prep_time['prep_hours'] * 60) + extracted_prep_time['prep_minutes']
    df['cook_time_mins'] = (extracted_cook_time['cook_hours'] * 60) + extracted_cook_time['cook_minutes']
    df['prep_time_mins'] = df['prep_time_mins'].astype(int)
    df['cook_time_mins'] = df['cook_time_mins'].astype(int)

    # Drop the old 'total_time' column
    df = df.drop(columns=['total_time'])

    # Filter out outliers (keeping recipes under 3 hours / 180 mins)
    df = df[df['minutes'] <= 180]
    df = df[df['minutes'] > 0]

    # Clean the R-style vector strings
    print("Cleaning ingredient and instruction formatting...")
    cols_to_clean = ['tags', 'ingredients', 'instructions', 'description']
    for col in cols_to_clean:
        df[col] = df[col].apply(clean_r_vector_string)

    print(f"Filtered down to {len(df)} highly usable recipes.")
    print(df[['name', 'minutes', 'ingredients']].head(3))

    # Upload to Supabase Postgres
    if not SUPABASE_URL:
        print("SUPABASE_DB_URL not found. Saving locally.")
        df.to_csv("clean_recipes.csv", index=False)
        return

    print("Connecting to Supabase and uploading 'recipes' table...")
    engine = create_engine(SUPABASE_URL)
    
    try:
        df.to_sql('recipes', engine, if_exists='replace', index=False, chunksize=5000)
        print("Successfully uploaded 'recipes' table to Supabase!")
        
    except Exception as e:
        print(f"Error uploading to Supabase: {e}")

if __name__ == "__main__":
    build_recipe_database()