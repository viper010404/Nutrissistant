import pandas as pd
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_DB_URL")

if SUPABASE_URL and SUPABASE_URL.startswith("postgres://"):
    SUPABASE_URL = SUPABASE_URL.replace("postgres://", "postgresql://", 1)

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