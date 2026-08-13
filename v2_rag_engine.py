import os
import uuid
import docx
import chromadb
from sentence_transformers import CrossEncoder

# Configuration
V2_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded_file_v2")
V2_CHROMA_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v2_chroma_db")

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 300
INITIAL_TOP_K = 20
RERANKED_TOP_K = 6
RELEVANCE_THRESHOLD = -5.0  # ms-marco scores are usually logits, > 0 is very good, -5 is a relaxed cutoff

# Initialize clients lazily to save memory on startup
_chroma_client = None
_cross_encoder = None

def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        os.makedirs(V2_CHROMA_DB_DIR, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=V2_CHROMA_DB_DIR)
    return _chroma_client

def get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        # Downloads model on first run (~90MB)
        _cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', max_length=512)
    return _cross_encoder

def extract_text_from_docx(file_path):
    doc = docx.Document(file_path)
    full_text = []
    for para in doc.paragraphs:
        if para.text.strip():
            full_text.append(para.text.strip())
    return "\n".join(full_text)

def create_overlapping_chunks(text, chunk_size, overlap):
    chunks = []
    start = 0
    text_len = len(text)
    
    while start < text_len:
        end = start + chunk_size
        
        # Adjust end to nearest word boundary if not at the end of text
        if end < text_len:
            while end > start and text[end] not in [' ', '\n', '\t']:
                end -= 1
            if end == start: # Word is longer than chunk size, just cut it
                end = start + chunk_size
                
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
            
        start = end - overlap
        
    return chunks

def ingest_file_v2(file_path, filename):
    """Parses file, chunks it, and stores in ChromaDB."""
    print(f"V2 Ingesting: {filename}")
    text = ""
    if filename.endswith(".docx"):
        text = extract_text_from_docx(file_path)
    elif filename.endswith(".txt"):
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
    else:
        raise ValueError("Unsupported file type for V2. Use .docx or .txt")

    if not text.strip():
        raise ValueError("File is empty or could not be read.")

    chunks = create_overlapping_chunks(text, CHUNK_SIZE, CHUNK_OVERLAP)
    
    client = get_chroma_client()
    collection = client.get_or_create_collection(name="rrams_v2_collection")
    
    # Optional: Clear old data for this filename to prevent duplicates on re-upload
    try:
        collection.delete(where={"filename": filename})
    except Exception:
        pass
        
    ids = [str(uuid.uuid4()) for _ in chunks]
    metadatas = [{"filename": filename, "chunk_index": i} for i in range(len(chunks))]
    
    # By default, ChromaDB uses all-MiniLM-L6-v2 for embeddings if we pass texts
    collection.add(
        documents=chunks,
        metadatas=metadatas,
        ids=ids
    )
    
    return {"status": "success", "chunks_added": len(chunks)}

def retrieve_and_rerank(query):
    """Retrieves top K chunks, reranks them, and applies threshold."""
    client = get_chroma_client()
    collection = client.get_or_create_collection(name="rrams_v2_collection")
    
    if collection.count() == 0:
        return []
        
    # 1. Initial Vector Retrieval (Top 12)
    results = collection.query(
        query_texts=[query],
        n_results=min(INITIAL_TOP_K, collection.count())
    )
    
    candidates = results['documents'][0]
    
    if not candidates:
        return []
        
    # 2. Cross-Encoder Reranking
    encoder = get_cross_encoder()
    pairs = [[query, chunk] for chunk in candidates]
    scores = encoder.predict(pairs)
    
    # 3. Zip and Sort by Score
    scored_candidates = list(zip(candidates, scores))
    scored_candidates.sort(key=lambda x: x[1], reverse=True)
    
    # 4. Apply Threshold and Select Top 4
    final_chunks = []
    for chunk, score in scored_candidates:
        if score > RELEVANCE_THRESHOLD:
            final_chunks.append(chunk)
            if len(final_chunks) >= RERANKED_TOP_K:
                break
                
    return final_chunks
