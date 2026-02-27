import requests
import json

MAX_RESULTS = 1500

def get_relevant_pmcids(search_query, max_results):
    """
    Queries the NCBI E-utilities API to retrieve PMCIDs matching a search string.
    """
    # NCBI ESearch base URL
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    
    params = {
        "db": "pmc",            # Target the PubMed Central database
        "term": search_query,   
        "retmode": "json",      
        "retmax": max_results   
    }

    print(f"Pinging NCBI E-utilities API for up to {max_results} papers...")
    
    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        
        data = response.json()
        id_list = data.get("esearchresult", {}).get("idlist", [])
        
        if not id_list:
            print("No PMCIDs found matching the query.")
            return []
            
        print(f"Successfully retrieved {len(id_list)} PMCIDs.")
        return id_list

    except requests.exceptions.RequestException as e:
        print(f"An error occurred while connecting to the API: {e}")
        return []

def save_to_json(data, filename="pmcids.json"):
    """Saves the given data to a JSON file."""
    try:
        with open(filename, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Successfully saved {len(data)} IDs to {filename}")
    except Exception as e:
        print(f"Error saving to JSON: {e}")


if __name__ == "__main__":
    nutrition_query = (
        '("Diet, Healthy"[MeSH Terms] OR "Diet, Reducing"[MeSH Terms] OR '
        '"Diet Therapy"[MeSH Terms] OR "Nutritional Physiological Phenomena"[MeSH Terms] OR '
        '"meal planning"[Title/Abstract] OR "meal plan*"[Title/Abstract] OR '
        '"dietary pattern*"[Title/Abstract] OR "healthy eating"[Title/Abstract] OR '
        '"macronutrient*"[Title/Abstract] OR "precision nutrition"[Title/Abstract] OR '
        '"weight management"[Title/Abstract] OR "weight loss"[Title/Abstract] OR '
        '"weight gain"[Title/Abstract] OR "caloric restriction"[Title/Abstract]) '
        'AND ("open access"[filter]) '
        'NOT ("exercise"[Title] OR "workout*"[Title] OR "sports"[Title] OR "resistance training"[Title])'
    )

    workouts_query = (
        '("Exercise"[MeSH Terms] OR "Resistance Training"[MeSH Terms] OR '
        '"Physical Fitness"[MeSH Terms] OR "High-Intensity Interval Training"[MeSH Terms] OR '
        '"exercise prescription"[Title/Abstract] OR "workout routine*"[Title/Abstract] OR '
        '"training volume"[Title/Abstract] OR "training frequency"[Title/Abstract] OR '
        '"periodization"[Title/Abstract] OR "resistance exercise*"[Title/Abstract] OR '
        '"aerobic capacity"[Title/Abstract] OR "warm-up"[Title/Abstract] OR '
        '"cool-down"[Title/Abstract] OR "rest interval*"[Title/Abstract] OR '
        '"repetition maximum"[Title/Abstract] OR "muscle hypertrophy"[Title/Abstract]) '
        'AND ("open access"[filter]) '
        'NOT ("Diet"[Title] OR "Nutrition*"[Title] OR "Meal"[Title] OR "Dietary"[Title])'
    )
    
    pmcid_list = get_relevant_pmcids(workouts_query, MAX_RESULTS)

    # Save the results
    if pmcid_list:
        save_to_json(pmcid_list, "pmcids_workouts.json")