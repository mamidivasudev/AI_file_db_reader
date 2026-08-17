import os
import uuid
import docx
import chromadb
from sentence_transformers import CrossEncoder
import httpx


# Configuration
V2_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded_file_v2")
V2_CHROMA_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v2_chroma_db")

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 300
INITIAL_TOP_K = 20
RERANKED_TOP_K = 6
RELEVANCE_THRESHOLD = -8.0  # ms-marco scores are usually logits, > 0 is very good, -5 is a relaxed cutoff

# Initialize clients lazily to save memory on startup
_chroma_client = None
_cross_encoder = None

from chromadb.utils import embedding_functions

# Multilingual Embedding Function for ChromaDB
multilingual_ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="paraphrase-multilingual-MiniLM-L12-v2")

def needs_translation(text: str) -> str:
    """Detects Telugu or Hindi (Devanagari) script and returns the language name."""
    for ch in text:
        code = ord(ch)
        if 0x0C00 <= code <= 0x0C7F:   # Telugu block
            return "Telugu"
        if 0x0900 <= code <= 0x097F:   # Devanagari (Hindi) block
            return "Hindi"
    return ""

TRANSLATION_MODEL = "llama3:latest"

async def translate_to_english(text: str, model: str = TRANSLATION_MODEL) -> str:
    """Translates Telugu/Hindi query to English using aya:8b before retrieval."""
    payload = {
        "model": TRANSLATION_MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional translator. Translate the user's message to English. Respond with ONLY the English translation — no quotes, no explanation, no preamble."},
            {"role": "user", "content": text}
        ],
        "stream": False,
        "options": {"temperature": 0.0}
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("http://localhost:11434/api/chat", json=payload, timeout=60.0)
            response.raise_for_status()
            data = response.json()
            translated = data.get("message", {}).get("content", "").strip()
            return translated if translated else text
    except Exception as e:
        return text  # fall back to original query if translation fails

async def translate_from_english(text: str, target_language: str) -> str:
    """Translates an English answer back to the target language using aya:8b."""
    payload = {
        "model": TRANSLATION_MODEL,
        "messages": [
            {"role": "system", "content": f"You are a professional translator. Translate the following English text to {target_language}. Respond with ONLY the {target_language} translation — no quotes, no explanation, no English words mixed in."},
            {"role": "user", "content": text}
        ],
        "stream": False,
        "options": {"temperature": 0.0}
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("http://localhost:11434/api/chat", json=payload, timeout=60.0)
            response.raise_for_status()
            data = response.json()
            translated = data.get("message", {}).get("content", "").strip()
            return translated if translated else text
    except Exception:
        return text  # fall back to English if translation fails


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        os.makedirs(V2_CHROMA_DB_DIR, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=V2_CHROMA_DB_DIR)
    return _chroma_client

def get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        # Cross-Encoder (Multilingual)
        _cross_encoder = CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1', max_length=512)
    return _cross_encoder

def extract_text_from_docx(file_path):
    doc = docx.Document(file_path)
    parts = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if para.style.name.startswith("Heading"):
            parts.append(f"\n\n## SECTION: {text}\n")
        else:
            parts.append(text)
    return "\n".join(parts)

def create_overlapping_chunks(text, chunk_size, overlap):
    chunks = []
    sections = text.split("## SECTION: ")
    for idx, section_text in enumerate(sections):
        section_text = section_text.strip()
        if not section_text:
            continue
            
        section_title = section_text.split("\n")[0][:100] if idx > 0 else "Introduction"
        full_text = f"[{section_title}] " + section_text if idx > 0 else section_text
        
        start = 0
        text_len = len(full_text)
        
        while start < text_len:
            end = start + chunk_size
            if end < text_len:
                while end > start and full_text[end] not in [' ', '\n', '\t']:
                    end -= 1
                if end == start:
                    end = start + chunk_size
            
            chunk = full_text[start:end].strip()
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
    
    # Wipe the entire collection to ensure strict single-document mode
    try:
        client.delete_collection(name="rrams_multilingual_v2")
    except Exception:
        pass
        
    collection = client.get_or_create_collection(
        name="rrams_multilingual_v2",
        embedding_function=multilingual_ef
    )
        
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
    collection = client.get_or_create_collection(
        name="rrams_multilingual_v2",
        embedding_function=multilingual_ef
    )
    
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
