import os
import sys
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv
import pandas as pd
import json
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_openai import OpenAIEmbeddings
from pinecone import Pinecone

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.query_DB import query_database, parse_recipes_query_result
from constants import RECIPE_CSV_PATH, RECIPE_FOR_VECTOR_DB_PATH
load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME_RECIPES = os.getenv("PINECONE_INDEX_NAME_RECIPES")
LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")

BATCH_SIZE = 256
MAX_WORKERS = int(os.getenv("RECIPE_EMBED_WORKERS", "4"))

LIMIT = None


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

def filter_recepie_data():
    # Load the raw dataset
    print("Loading recipes.csv...")
    
    # Use the exact column names from CSV
    target_columns = [
        'RecipeId', 'Name', 'TotalTime', 'Keywords',
        'RecipeIngredientParts', 'RecipeCategory', 'Description',
        'RecipeInstructions'
    ]
    
    df = pd.read_csv(RECIPE_CSV_PATH, usecols=target_columns)

    # Rename columns to be lowercased and SQL-friendly
    df = df.rename(columns={
        'RecipeId': "id",
        'Name': 'name',
        'TotalTime': 'total_time',
        'Keywords': 'tags',
        'RecipeIngredientParts': 'ingredients',
        'RecipeCategory': 'category',
        'Description': 'description',
        'RecipeInstructions': 'instructions'
    })

    # Drop rows with missing critical data
    df = df.dropna(subset=['name', 'total_time', 'ingredients', 'instructions', 'description'])

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

    
    # Drop the old 'total_time' column
    df = df.drop(columns=['total_time'])

    # Filter out outliers (keeping recipes under 3 hours / 180 mins)
    df = df[df['minutes'] <= 180]
    df = df[df['minutes'] > 0]

    # Clean the R-style vector strings
    print("Cleaning ingredient and instruction formatting...")
    cols_to_clean = ['tags', 'ingredients', 'description', 'instructions']
    for col in cols_to_clean:
        df[col] = df[col].apply(clean_r_vector_string)

    print(f"Filtered down to {len(df)} highly usable recipes.")
    return df


def parse_recipe_as_json(recipe: Dict[str, Any]) -> str:
    """
    Converts a recipe dictionary into a structured JSON string for embedding.
    """
    def safe_text(value: Any, default: str = "") -> str:
        if value is None or pd.isna(value):
            return default
        return str(value).strip()

    def safe_list_from_csv(value: Any) -> list[str]:
        text = safe_text(value, "")
        if not text:
            return []
        return [item.strip() for item in text.split(",") if item and item.strip()]

    
    recipe_json = {
        "name": safe_text(recipe.get("name", "")),
        "category": safe_text(recipe.get("category", "")),
        "tags": safe_list_from_csv(recipe.get("tags", "")),
        "ingredients": safe_list_from_csv(recipe.get("ingredients", "")),
        "description": safe_text(recipe.get("description", "")),
        # we chose to not inlude numerical columns since they arent really helpfull for getting recepie
        # we chose not to include instructions since they are very long and might tilt the vectors from the influence of other columns
        }
    return json.dumps(recipe_json, allow_nan=False)


def create_recepie_csv():
    df = filter_recepie_data()
    df["recipe_string"] = df.apply(lambda row: parse_recipe_as_json(row), axis=1)
    df_new = df[["id", "recipe_string"]]
    df_new.to_csv(RECIPE_FOR_VECTOR_DB_PATH, index=False)
    print(f"Cleaned recipe data saved to {RECIPE_FOR_VECTOR_DB_PATH}")


def embed_and_upload_to_pinecone():
    if not PINECONE_API_KEY or not PINECONE_INDEX_NAME_RECIPES:
        raise ValueError("Missing Pinecone configuration in environment variables.")

    if not LLMOD_API_KEY:
        raise ValueError("Missing embedding API key (LLMOD_API_KEY).")

    csv_path = Path(RECIPE_FOR_VECTOR_DB_PATH)
    if not csv_path.exists():
        print(f"{csv_path} not found. Generating it first...")
        create_recepie_csv()

    df = pd.read_csv(csv_path)
    if "id" not in df.columns or "recipe_string" not in df.columns:
        raise ValueError("Expected columns ['id', 'recipe_string'] in recipe vector CSV.")

    df = df.dropna(subset=["id", "recipe_string"])

    if df.empty:
        print("No recipes to embed.")
        return

    embedding_kwargs = {
        "model": "RPRTHPB-text-embedding-3-small",
        "api_key": LLMOD_API_KEY,
    }
    if OPENAI_API_BASE:
        embedding_kwargs["base_url"] = OPENAI_API_BASE

    embeddings = OpenAIEmbeddings(**embedding_kwargs)

    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX_NAME_RECIPES)

    total = len(df)
    print(f"Embedding and uploading {total} recipes in batches of {BATCH_SIZE}...")

    def process_batch(start: int):
        batch = df.iloc[start:start + BATCH_SIZE]
        ids = batch["id"].astype(str).tolist()
        texts = batch["recipe_string"].astype(str).tolist()

        vectors = embeddings.embed_documents(texts)
        upsert_payload = [
            {
                "id": rid,
                "values": vec,
            }
            for rid, vec in zip(ids, vectors)
        ]

        index.upsert(vectors=upsert_payload)
        return len(ids)

    batch_starts = list(range(0, total, BATCH_SIZE))
    uploaded_count = 0

    with ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS)) as executor:
        futures = [executor.submit(process_batch, start) for start in batch_starts]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Upserting batches"):
            uploaded_count += future.result()

    print(f"Done uploading {uploaded_count} recipe embeddings to Pinecone.")

if __name__ == "__main__":
    embed_and_upload_to_pinecone()