import requests
import json
import time
import os

def fetch_and_clean_paper(pmcid):
    """
    Fetches a paper from the BioC API and extracts only high-value sections.
    """
    formatted_id = f"PMC{pmcid}" if not str(pmcid).startswith("PMC") else pmcid
    url = f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{formatted_id}/unicode"
    
    try:
        response = requests.get(url)
        
        if response.status_code != 200:
            print(f"  -> Skipping {formatted_id}: API error (Status {response.status_code})")
            return None
            
        # SAFELY ATTEMPT TO PARSE JSON
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            print(f"  -> Skipping {formatted_id}: Not yet processed by BioC database (returned plain text).")
            return None
            
        # If the API returned a list instead of a dictionary, extract the first item.
        if isinstance(data, list):
            if len(data) > 0:
                data = data[0]
            else:
                print(f"  -> Skipping {formatted_id}: API returned an empty list.")
                return None
                
        # If it's STILL not a dictionary after unwrapping, skip it to avoid crashing.
        if not isinstance(data, dict):
            print(f"  -> Skipping {formatted_id}: Unexpected data format.")
            return None

        extracted_sections = {
            "abstract": [],
            "introduction": [],
            "results": [],
            "discussion": [],
            "conclusion": []
        }
        
        target_keys = extracted_sections.keys()
        
        for document in data.get("documents", []):
            for passage in document.get("passages", []):
                section_type = passage.get("infons", {}).get("section_type", "").lower()
                
                for key in target_keys:
                    if key in section_type or section_type.startswith(key[:5]):
                        extracted_sections[key].append(passage.get("text", ""))
                        break
                        
        clean_text = ""
        for section, texts in extracted_sections.items():
            if texts:
                clean_text += f"\n\n--- {section.upper()} ---\n\n"
                clean_text += " ".join(texts)
                
        if not clean_text.strip():
            print(f"  -> Skipping {formatted_id}: No target sections found in text.")
            return None
            
        return {
            "pmcid": formatted_id,
            "text": clean_text.strip()
        }
        
    except Exception as e:
        print(f"  -> Error processing {formatted_id}: {e}")
        return None

def process_pipeline(input_file="pmcids_workouts.json", output_file="cleaned_papers_workouts.json"):
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found. Run Phase 1 first.")
        return
        
    with open(input_file, 'r') as f:
        pmcid_list = json.load(f)
        
    print(f"Loaded {len(pmcid_list)} PMCIDs. Beginning extraction...")
    
    cleaned_papers = []
    success_count = 0
    
    for idx, pmcid in enumerate(pmcid_list):
        print(f"Processing {idx + 1}/{len(pmcid_list)}: {pmcid}")
        
        paper_data = fetch_and_clean_paper(pmcid)
        if paper_data:
            cleaned_papers.append(paper_data)
            success_count += 1
            
        time.sleep(0.35) 
        
    with open(output_file, 'w') as f:
        json.dump(cleaned_papers, f, indent=4)
        
    print(f"\nPipeline complete! Successfully extracted {success_count} out of {len(pmcid_list)} papers.")
    print(f"Saved highly-relevant papers to {output_file}.")

if __name__ == "__main__":
    process_pipeline()