import os
import json
from tqdm import tqdm
from dotenv import load_dotenv

from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pinecone import Pinecone

load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")
LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")

JSON_PATH = "cleaned_papers.json"  
CHUNK_SIZE = 1024             
CHUNK_OVERLAP = 120            
BATCH_SIZE = 100               

LIMIT = None

def ingest_data():
    print("Starting RAG ingestion pipeline for the Meal Planner...")

    pc = Pinecone(api_key=PINECONE_API_KEY)
    
    if PINECONE_INDEX_NAME not in [i.name for i in pc.list_indexes()]:
        print(f"Index '{PINECONE_INDEX_NAME}' not found in Pinecone!")
        print("Please create it in the Pinecone console with Dimension=1536, Metric=cosine.")
        return
    
    index = pc.Index(PINECONE_INDEX_NAME)
    print(f"Connected to Pinecone Index: {PINECONE_INDEX_NAME}")

    # Initialize Embedding Model via LLMod.ai
    embeddings = OpenAIEmbeddings(
        api_key=LLMOD_API_KEY,
        base_url=OPENAI_API_BASE,
        model="RPRTHPB-text-embedding-3-small"
    )

    # Load Data
    if not os.path.exists(JSON_PATH):
        print(f"Error: {JSON_PATH} not found. Please run Phase 2 first.")
        return

    print(f"Loading {JSON_PATH}...")
    with open(JSON_PATH, 'r') as f:
        papers_data = json.load(f)
    
    # Safety Limit
    if LIMIT:
        print(f"TEST MODE: Processing only first {LIMIT} papers.")
        papers_data = papers_data[:LIMIT]
    
    print(f"Processing {len(papers_data)} papers...")

    # Process & Chunk Data
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP
    )

    vectors_to_upsert = []
    
    # Iterate through each paper
    for paper in tqdm(papers_data, desc="Chunking Text"):
        pmcid = paper.get('pmcid')
        text = paper.get('text')

        if not text:
            continue

        # Split the cleaned paper sections into chunks
        chunks = text_splitter.split_text(text)

        for chunk_index, chunk_text in enumerate(chunks):
            # Create a unique ID for this chunk (e.g., "PMC123456_0")
            vector_id = f"{pmcid}_{chunk_index}"
            
            # Prepare metadata for the agent to retrieve
            metadata = {
                "pmcid": pmcid,
                "chunk_text": chunk_text, 
                "chunk_index": chunk_index
            }
            
            vectors_to_upsert.append({
                "id": vector_id,
                "text": chunk_text,
                "metadata": metadata
            })

    print(f"Generated {len(vectors_to_upsert)} chunks. Starting Embedding & Upload...")

    # Embed and Upsert in Batches
    total_vectors = len(vectors_to_upsert)
    
    for i in range(0, total_vectors, BATCH_SIZE):
        batch = vectors_to_upsert[i : i + BATCH_SIZE]
        
        # Extract just the text to embed
        texts_to_embed = [item["text"] for item in batch]
        
        try:
            # Generate Embeddings (Sends batch to LLMod)
            embeddings_list = embeddings.embed_documents(texts_to_embed)
            
            # Prepare for Pinecone
            pinecone_vectors = []
            for j, embedding in enumerate(embeddings_list):
                item = batch[j]
                pinecone_vectors.append({
                    "id": item["id"],
                    "values": embedding,
                    "metadata": item["metadata"]
                })
            
            # Upload to Pinecone
            index.upsert(vectors=pinecone_vectors)
            
            print(f"  -> {min(i + BATCH_SIZE, total_vectors)}/{total_vectors} vectors embedded and uploaded...", end="\r")
            
        except Exception as e:
            print(f"\nError processing batch {i}: {e}")
            continue

    print("\n\nIngestion Complete! The knowledge base is now live in Pinecone.")

if __name__ == "__main__":
    ingest_data()