import os
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from pinecone import Pinecone

# Load environment variables
load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME_NUTRITION = os.getenv("PINECONE_INDEX_NAME_NUTRITION")
LLMOD_API_KEY = os.getenv("LLMOD_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")

def test_rag_retrieval(query: str, top_k: int = 3):
    """
    Embeds a query and retrieves the most relevant chunks from the Pinecone vector database.
    """
    print(f"Connecting to Pinecone Index: {PINECONE_INDEX_NAME_NUTRITION}...")
    pc = Pinecone(api_key=PINECONE_API_KEY)
    
    # Check if index exists
    if PINECONE_INDEX_NAME_NUTRITION not in [i.name for i in pc.list_indexes()]:
        print(f"Error: Index '{PINECONE_INDEX_NAME_NUTRITION}' not found.")
        return
        
    index = pc.Index(PINECONE_INDEX_NAME_NUTRITION)

    print("Initializing embedding model...")
    # Initialize the same embedding model used during ingestion
    embeddings = OpenAIEmbeddings(
        api_key=LLMOD_API_KEY,
        base_url=OPENAI_API_BASE,
        model="RPRTHPB-text-embedding-3-small"
    )

    print(f"\nEmbedding query: '{query}'")
    try:
        # Embed the user's question
        query_embedding = embeddings.embed_query(query)
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return

    print("Querying Pinecone database...")
    try:
        # Search the database for the closest vectors
        results = index.query(
            vector=query_embedding,
            top_k=top_k,
            include_metadata=True # Critical: this returns the 'chunk_text' we saved
        )
        
        matches = results.get("matches", [])
        
        if not matches:
            print("No relevant context found.")
            return

        print("\n=== TOP RETRIEVED CONTEXTS ===")
        for i, match in enumerate(matches):
            score = match.get("score", 0)
            metadata = match.get("metadata", {})
            pmcid = metadata.get("pmcid", "Unknown")
            chunk_text = metadata.get("chunk_text", "No text available.")
            
            print(f"\n--- Result {i+1} | Score: {score:.4f} | Source: PMC{pmcid} ---")
            print(f"{chunk_text}\n")
            print("-" * 50)

    except Exception as e:
        print(f"Error querying Pinecone: {e}")

if __name__ == "__main__":
    # The toy example prompt
    test_question = "Is Iron crucial for my health?"
    test_rag_retrieval(test_question, top_k=5)