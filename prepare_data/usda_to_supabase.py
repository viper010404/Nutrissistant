import pandas as pd
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_DB_URL")

if SUPABASE_URL and SUPABASE_URL.startswith("postgres://"):
    SUPABASE_URL = SUPABASE_URL.replace("postgres://", "postgresql://", 1)

def build_usda_database():
    # Load and Filter Foods
    print("Loading food.csv...")
    df_food = pd.read_csv("food.csv", usecols=["fdc_id", "data_type", "description"])
    
    target_types = ['sr_legacy_food', 'foundation_food', 'survey_fndds_food']
    df_food = df_food[df_food['data_type'].isin(target_types)]
    print(f"Filtered down to {len(df_food)} high-quality core foods.")

    if df_food.empty:
        print("No foods matched the filter. Check the data_type values in food.csv.")
        return

    target_nutrients = {
        # CALORIES
        1008: 'calories', # Standard Energy
        2047: 'calories', # Energy (Atwater General Factors)
        2048: 'calories', # Energy (Atwater Specific Factors)

        # PROTEIN
        1003: 'protein_g', # Standard Protein
        
        # FAT
        1004: 'fat_g', # Total lipid (fat)
        1085: 'fat_g', # Total fat (NLEA labeling standard)
        
        # CARBOHYDRATES
        1005: 'carbs_g', # Carbohydrate, by difference (most common)
        1050: 'carbs_g', # Carbohydrate, by summation
        2039: 'carbs_g', # Carbohydrates (newer general ID)
        
        # FIBER
        1079: 'fiber_g', # Fiber, total dietary (most common)
        2033: 'fiber_g'  # Total dietary fiber (Newer AOAC 2011.25 standard)
    }

    # Load and Filter Food Nutrients
    print("Loading food_nutrient.csv (This might take a minute)...")
    df_nutrients = pd.read_csv(
        "food_nutrient.csv", 
        usecols=["fdc_id", "nutrient_id", "amount"]
    )
    
    df_nutrients = df_nutrients[
        (df_nutrients['nutrient_id'].isin(target_nutrients.keys())) & 
        (df_nutrients['fdc_id'].isin(df_food['fdc_id']))
    ]
    print(f"Filtered nutrients down to {len(df_nutrients)} relevant macro records.")

    if df_nutrients.empty:
        print("No nutrient records found for these foods.")
        return

    # Map Nutrient IDs to Readable Names
    df_nutrients['nutrient_name'] = df_nutrients['nutrient_id'].map(target_nutrients)

    # Pivot the Table (Wide Format)
    print("Pivoting data into wide format...")
    # Using aggfunc='max' ensures that if a food has both ID 1008 and 2047, 
    # we take the highest valid number instead of crashing.
    
    df_pivoted = df_nutrients.pivot_table(
        index='fdc_id', 
        columns='nutrient_name', 
        values='amount', 
        aggfunc='max'
    ).reset_index()

    df_pivoted = df_pivoted.fillna(-1)

    # Merge Foods with Pivoted Nutrients
    print("Merging foods with nutrient data...")
    final_df = pd.merge(df_food, df_pivoted, on='fdc_id', how='inner')
    
    final_df = final_df.drop(columns=['data_type'])
    final_df = final_df.rename(columns={'description': 'food_name'})
    
    numeric_cols = ['calories', 'protein_g', 'fat_g', 'carbs_g', 'fiber_g']
            
    final_df[numeric_cols] = final_df[numeric_cols].round(2)

    print(f"Final Dataset Ready! Shape: {final_df.shape}")
    print(final_df.head())

    # Upload to Supabase Postgres
    if not SUPABASE_URL:
        print("SUPABASE_DB_URL not found in .env file. Saving to local CSV instead.")
        final_df.to_csv("clean_usda_foods.csv", index=False)
        return

    print("Connecting to Supabase and uploading table...")
    engine = create_engine(SUPABASE_URL)
    
    try:
        final_df.to_sql('usda_foods', engine, if_exists='replace', index=False)
        print("Successfully uploaded 'usda_foods' table to Supabase!")
        
    except Exception as e:
        print(f"Error uploading to Supabase: {e}")

if __name__ == "__main__":
    build_usda_database()