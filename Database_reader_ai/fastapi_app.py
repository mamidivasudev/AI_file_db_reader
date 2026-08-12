"""
FastAPI wrapper — MSSQL AI Assistant
Exposes the core logic as REST endpoints consumable by .NET / Java apps.

Endpoints:
  POST /connect          — test DB connection and return available tables
  POST /schema           — get schema text for selected tables
  POST /ask              — natural-language question → SQL → results → AI answer
  GET  /models           — list available Ollama models
  GET  /health           — liveness check

Auth: Bearer JWT (HS256). Set SECRET_KEY env var.
      Pass Authorization: Bearer <token> header on every request.

Run:
  pip install fastapi uvicorn python-jose[cryptography] pydantic
  uvicorn fastapi_app:app --host 0.0.0.0 --port 8000
"""

import os
import logging
from datetime import datetime, timezone
import tempfile
import shutil
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, status, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import json
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from mssql_connector import connect_mssql
from mssql_schema_reader import get_all_tables, get_selected_schema_text
from mssql_sql_generator import generate_tsql, generate_answer_summary
from mssql_executor import validate_tsql, execute_tsql
from ollama_client import list_ollama_models, ask_ollama, ask_ollama_stream
from audit_logger import log_query   # see audit_logger.py
from session_manager import (
    create_session,
    get_session,
    remove_session,
    get_file_session_history,
    add_file_session_history
)
from file_reader import read_project
from search_engine import search_files
# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mssql_api")

MULTILINGUAL_PROMPT_TEMPLATE = (
    "You are the official AI Assistant for this application.\n\n"
    "CRITICAL RULES:\n"
    "1. DIRECT & NATURAL ANSWERS ONLY: Begin your answer directly with facts and data. NEVER start answers with 'According to...', 'Based on...', 'According to the data...', 'The conversation context...', or similar intro phrases.\n"
    "2. STRICT NUMERICAL & DATA FACT ACCURACY: NEVER guess, estimate, or hallucinate numbers or statistics (e.g. 12,345 km, 8,765 km). Read exact numerical figures strictly from the data facts. If an exact number or count is not explicitly stated in the context, state that the specific detail is not available in our system rather than inventing fallback numbers or fake statistics.\n"
    "3. FULL STATE-LEVEL TOTALS: Always provide the full state-level totals (such as all 8 RIS dashboard charts) rather than partial sub-level counts.\n"
    "4. HIDE FILE & META REFERENCES: NEVER mention or use words like 'document', 'file', 'PDF', 'page', 'manual', 'section', 'chapter', 'appendix', 'text', 'provided information', 'provided context', 'provided data', 'conversation context', 'conversation history', 'prior messages', 'available data', 'dastavej', 'పత్రం', 'arquivo'.\n"
    "   - Present all information directly as facts.\n"
    "   - If asked where information comes from or about your source, answer naturally in plain words (e.g. 'I am the application AI assistant providing answers from our system database.') without repeating the exact same phrase across turns.\n"
    "5. NO REPETITION LOOPS: NEVER output the exact same sentence or response twice in a row across consecutive turns. Vary your wording naturally.\n"
    "6. FORMATTING & LISTS: Use clear line breaks and Markdown formatting (such as numbered lists 1., 2., 3. or bullet points) for multi-step processes or lists to ensure clean UI presentation.\n"
    "7. MISSING INFORMATION: If requested details are missing, state naturally in the user's language: 'This detail is currently not available in our system.' Do not comment on conversation context or previous turns.\n"
    "8. LANGUAGE MATCHING: Respond strictly in the exact same language as the user's question.\n"
    "9. CLICKABLE URLS: Output website URLs as clickable Markdown hyperlinks [URL](URL) or plain text URLs (e.g. [https://ssotest.rajasthan.gov.in/signin](https://ssotest.rajasthan.gov.in/signin) or https://ssotest.rajasthan.gov.in/signin). NEVER wrap URLs in backticks (`) or inline code blocks so that links remain active and clickable in the UI.\n\n"
)

app = FastAPI(
    title="MSSQL AI Assistant API",
    version="1.0.0",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(.*\.)?satragroup\.in",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# JWT Auth
# ─────────────────────────────────────────────
bearer_scheme = HTTPBearer()


# def verify_token(
#     credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
# ) -> dict:
#     token = credentials.credentials
#     try:
#         payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
#         return payload
#     except JWTError as exc:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail=f"Invalid or expired token: {exc}",
#             headers={"WWW-Authenticate": "Bearer"},
#         )

# TEMPORARY - Disable JWT Authentication for Testing
def verify_token():
    return {
        "sub": "test-user"
    }
# ─────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────
class ConnectRequest(BaseModel):
    server: str
    database: str
    auth_mode: str                    # "Windows Authentication" | "SQL Server Authentication"
    username: Optional[str] = None
    password: Optional[str] = None
    driver: Optional[str] = None


class SchemaRequest(BaseModel):
    session_id: str
    tables: list[str]

class AskRequest(BaseModel):
    session_id: str
    tables: list[str]
    question: str
    model: Optional[str] = None

class AskFilesRequest(BaseModel):
    session_id: Optional[str] = None
    question: str
    model: Optional[str] = None
    filename: Optional[str] = None

class ConnectResponse(BaseModel):
    session_id: str
    status: str
    database: str
    table_count: int
    tables: list[str]

class SchemaResponse(BaseModel):
    schema_text: str


class AskResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    answer: str

class AskFilesResponse(BaseModel):
    session_id: Optional[str] = None
    question: str
    answer: str


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _parse_table(label: str) -> tuple[str, str]:
    """'dbo.Orders' → ('dbo', 'Orders')"""
    parts = label.split(".", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=400,
            detail=f"Table '{label}' must be in 'schema.table' format.",
        )
    return parts[0], parts[1]


def _get_conn(req):
    try:
        return connect_mssql(
            server=req.server,
            database=req.database,
            auth_mode=req.auth_mode,
            username=req.username,
            password=req.password,
            driver=req.driver,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB connection failed: {exc}")


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/models")
def get_models(payload: dict = Depends(verify_token)):
    return {"models": list_ollama_models()}


@app.post("/connect", response_model=ConnectResponse)
def connect(req: ConnectRequest, payload: dict = Depends(verify_token)):

    conn = _get_conn(req)

    try:

        tables = get_all_tables(conn)

        table_labels = [
            f"{s}.{t}"
            for s, t in tables
        ]

        session_id = create_session({
            "server": req.server,
            "database": req.database,
            "auth_mode": req.auth_mode,
            "username": req.username,
            "password": req.password,
            "driver": req.driver
        })

        return ConnectResponse(
            session_id=session_id,
            status="connected",
            database=req.database,
            table_count=len(tables),
            tables=table_labels,
        )

    finally:

        conn.close()

@app.post("/schema", response_model=SchemaResponse)
def schema(req: SchemaRequest,
           payload: dict = Depends(verify_token)):

    session = get_session(req.session_id)

    if not session:

        raise HTTPException(
            status_code=404,
            detail="Invalid session."
        )

    conn = connect_mssql(
        server=session["server"],
        database=session["database"],
        auth_mode=session["auth_mode"],
        username=session["username"],
        password=session["password"],
        driver=session["driver"]
    )

    try:
        
        if not req.tables:
            raise HTTPException(
                status_code=400,
                detail="Provide at least one table.")
        selected = [
            _parse_table(t)
            for t in req.tables
        ]
    

        schema_text = get_selected_schema_text(
            conn,
            selected
        )

        return SchemaResponse(
            schema_text=schema_text
        )

    finally:

        conn.close()
@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, payload: dict = Depends(verify_token)):

    if not req.tables:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one table."
        )

    if not req.question.strip():
        raise HTTPException(
            status_code=400,
            detail="Question cannot be empty."
        )

    session = get_session(req.session_id)

    if not session:

        raise HTTPException(
            status_code=404,
            detail="Invalid session."
        )

    conn = connect_mssql(
        server=session["server"],
        database=session["database"],
        auth_mode=session["auth_mode"],
        username=session["username"],
        password=session["password"],
        driver=session["driver"]
    )

    user_id = payload.get("sub", "unknown")

    try:

        selected = [
            _parse_table(t)
            for t in req.tables
        ]

        schema_text = get_selected_schema_text(
            conn,
            selected
        )

        sql = generate_tsql(
            req.question,
            schema_text,
            model=req.model
        )

        is_safe, reason = validate_tsql(sql)

        if not is_safe:

            logger.warning(
                "Blocked query from user=%s: %s",
                user_id,
                reason
            )

            raise HTTPException(
                status_code=400,
                detail=f"Unsafe query blocked: {reason}"
            )

        columns, rows = execute_tsql(
            conn,
            sql
        )

        answer = generate_answer_summary(
            req.question,
            sql,
            columns,
            rows,
            model=req.model
        )

        log_query(
            user_id=user_id,
            question=req.question,
            sql=sql,
            row_count=len(rows),
            tables=req.tables,
            database=session["database"],
        )

        return AskResponse(
            question=req.question,
            sql=sql,
            columns=columns,
            rows=[list(r) for r in rows],
            row_count=len(rows),
            answer=answer,
        )

    except HTTPException:
        raise

    except Exception as exc:

        logger.error(
            "Error processing ask request: %s",
            exc
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )

    finally:

        conn.close()

class DisconnectRequest(BaseModel):
    session_id: str
    
@app.post("/disconnect")
def disconnect(
        req: DisconnectRequest,
        payload: dict = Depends(verify_token)
):

    remove_session(
        req.session_id
    )

    return {
        "status": "disconnected"
    }

@app.post("/ask-query", response_model=AskFilesResponse)
def ask_files(req: AskFilesRequest, payload: dict = Depends(verify_token)):

    if not req.question.strip():
        raise HTTPException(
            status_code=400,
            detail="Question cannot be empty."
        )

    try:
        # 1. Read files from the static directory
        static_dir = r"\\SAT-HYD-W0007\Vasu\New folder\Database_reader_ai\files"
        files_data = read_project(static_dir)
        
        if not files_data:
            raise HTTPException(
                status_code=404,
                detail=f"No files found in {static_dir}"
            )
            
        if req.filename:
            files_data = [f for f in files_data if req.filename.lower() in f["filename"].lower()]
            if not files_data:
                raise HTTPException(
                    status_code=404,
                    detail=f"File matching '{req.filename}' not found."
                )
            
        # 2. Search files
        matched_files = search_files(req.question, files_data)
        
        # Fallback: If a specific filename was requested, use it even if keyword search matched 0 words (e.g. Hindi/Gujarati/Hinglish questions)
        if not matched_files:
            if req.filename and files_data:
                matched_files = files_data
            else:
                return AskFilesResponse(
                    question=req.question,
                    answer="No relevant information found in the documents."
                )
            
        # 3. Build prompt
        prompt = (
            "You are an AI assistant answering questions based on provided document context.\n"
            "INSTRUCTION: Answer the question accurately using ONLY the provided document content.\n"
            "IMPORTANT: Respond in the same language as the user's Question (e.g., if asked in Hindi, respond in Hindi).\n"
            "DO NOT announce or write the name of the language in your response.\n\n"
        )
        for file in matched_files:
            prompt += f"FILE: {file['filename']}\n"
            prompt += file["content"][:80000] + "\n\n"
        prompt += f"Question:\n{req.question}"
        
        # 4. Ask Ollama
        if req.model:
            answer = ask_ollama(prompt, model=req.model)
        else:
            answer = ask_ollama(prompt)
        
        return AskFilesResponse(
            question=req.question,
            answer=answer
        )
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error processing ask-files request: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/upload-file-ask-query", response_model=AskFilesResponse)
async def upload_ask_query(
    question: str = Form(...),
    model: Optional[str] = Form(None),
    file: UploadFile = File(...),
    payload: dict = Depends(verify_token)
):
    if not question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
        
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file_path = os.path.join(temp_dir, file.filename)
            with open(temp_file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
                
            files_data = read_project(temp_dir)
            
            if not files_data:
                raise HTTPException(status_code=400, detail="Could not read the uploaded file.")
                
            matched_files = search_files(question, files_data)
            
            if not matched_files:
                matched_files = files_data
                
            prompt = MULTILINGUAL_PROMPT_TEMPLATE
            for f in matched_files:
                prompt += f"SYSTEM KNOWLEDGE CONTEXT:\n"
                prompt += f["content"][:80000] + "\n\n"
            prompt += f"Question:\n{question}"
            
            if model:
                answer = ask_ollama(prompt, model=model)
            else:
                answer = ask_ollama(prompt)
                
            return AskFilesResponse(
                question=question,
                answer=answer
            )
            
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error processing upload_ask_query request: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/is-file-present")
async def is_file_present():
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded_file")
    if not os.path.exists(upload_dir):
        return {"status": False, "file name": None}
    files = [f for f in os.listdir(upload_dir) if os.path.isfile(os.path.join(upload_dir, f))]
    if len(files) > 0:
        return {"status": True, "file name": files[0]}
    return {"status": False, "file name": None}

@app.post("/upload-file")
async def upload_file_endpoint(file: UploadFile = File(...)):
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded_file")
    os.makedirs(upload_dir, exist_ok=True)
    
    for f in os.listdir(upload_dir):
        file_path = os.path.join(upload_dir, f)
        if os.path.isfile(file_path):
            os.remove(file_path)
            
    dest_path = os.path.join(upload_dir, file.filename)
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"message": "File uploaded successfully", "filename": file.filename}

@app.post("/ask-your-query", response_model=AskFilesResponse)
async def ask_your_query(
    question: str = Form(...),
    model: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    payload: dict = Depends(verify_token)
):
    if not question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
        
    try:
        import uuid
        if not session_id or not session_id.strip():
            session_id = str(uuid.uuid4())

        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded_file")
        if not os.path.exists(upload_dir) or not [f for f in os.listdir(upload_dir) if os.path.isfile(os.path.join(upload_dir, f))]:
            raise HTTPException(status_code=400, detail="No file found in uploaded_file folder.")
            
        files_data = read_project(upload_dir)
        
        if not files_data:
            raise HTTPException(status_code=400, detail="Could not read the uploaded file.")
            
        matched_files = search_files(question, files_data)
        
        if not matched_files:
            matched_files = files_data

        history = get_file_session_history(session_id)
            
        prompt = MULTILINGUAL_PROMPT_TEMPLATE
        for f in matched_files:
            prompt += f"SYSTEM KNOWLEDGE CONTEXT:\n"
            prompt += f["content"][:80000] + "\n\n"

        if history:
            prompt += "Prior Messages:\n"
            for item in history:
                prompt += f"User Question: {item['question']}\nAI Answer: {item['answer']}\n\n"

        prompt += f"Current Question:\n{question}"
        
        if model:
            answer = ask_ollama(prompt, model=model)
        else:
            answer = ask_ollama(prompt)

        add_file_session_history(session_id, question, answer)
            
        return AskFilesResponse(
            session_id=session_id,
            question=question,
            answer=answer
        )
            
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error processing ask-your-query request: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/ask-your-query-stream")
async def ask_your_query_stream_endpoint(
    request: Request,
    question: str = Form(...),
    model: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    payload: dict = Depends(verify_token)
):
    if not question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
        
    try:
        import uuid
        if not session_id or not session_id.strip():
            session_id = str(uuid.uuid4())

        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded_file")
        if not os.path.exists(upload_dir) or not [f for f in os.listdir(upload_dir) if os.path.isfile(os.path.join(upload_dir, f))]:
            raise HTTPException(status_code=400, detail="No file found in uploaded_file folder.")
            
        files_data = read_project(upload_dir)
        
        if not files_data:
            raise HTTPException(status_code=400, detail="Could not read the uploaded file.")
            
        matched_files = search_files(question, files_data)
        
        if not matched_files:
            matched_files = files_data

        history = get_file_session_history(session_id)
            
        prompt = MULTILINGUAL_PROMPT_TEMPLATE
        for f in matched_files:
            prompt += f"SYSTEM KNOWLEDGE CONTEXT:\n"
            prompt += f["content"][:80000] + "\n\n"

        if history:
            prompt += "Prior Messages:\n"
            for item in history:
                prompt += f"User Question: {item['question']}\nAI Answer: {item['answer']}\n\n"

        prompt += f"Current Question:\n{question}"
        
        async def event_generator():
            try:
                # 1. Yield session ID
                yield f"event: session\ndata: {json.dumps({'session_id': session_id})}\n\n"
                
                full_answer = ""
                # 2. Yield chunks
                stream = ask_ollama_stream(prompt, model=model) if model else ask_ollama_stream(prompt)
                async for chunk in stream:
                    if await request.is_disconnected():
                        logger.warning("Client disconnected during stream. Stopping generation.")
                        break
                    
                    full_answer += chunk
                    yield f"event: delta\ndata: {json.dumps({'text': chunk})}\n\n"
                    
                # 3. Add to history
                if not await request.is_disconnected():
                    add_file_session_history(session_id, question, full_answer)
                    
                    # 4. Yield done
                    yield f"event: done\ndata: {json.dumps({'session_id': session_id})}\n\n"
                    
            except Exception as e:
                logger.error("Error during streaming generation: %s", e)
                yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error setting up ask-your-query-stream request: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))