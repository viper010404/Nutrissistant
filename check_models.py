import requests

def list_available_models(api_key):
    # LLMod uses standard OpenAI API routing
    url = "https://api.llmod.ai/v1/models"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        models_data = response.json().get("data", [])
        
        print(f"Successfully retrieved {len(models_data)} models. Available models:\n")
        
        # Sort alphabetically for easier reading
        model_ids = sorted([model["id"] for model in models_data])
        for model_id in model_ids:
            print(f"- {model_id}")
            
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to LLMod API: {e}")

if __name__ == "__main__":
    # Insert the shared group LLMod API key here
    LLMOD_API_KEY = "sk-SslThmxFDTDqS2H16uzG8w"
    list_available_models(LLMOD_API_KEY)