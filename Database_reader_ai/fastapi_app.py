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

from fastapi import Depends, FastAPI, HTTPException, status, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from mssql_connector import connect_mssql
from mssql_schema_reader import get_all_tables, get_selected_schema_text
from mssql_sql_generator import generate_tsql, generate_answer_summary
from mssql_executor import validate_tsql, execute_tsql
from ollama_client import list_ollama_models, ask_ollama
from audit_logger import log_query   # see audit_logger.py
from session_manager import (
    create_session,
    get_session,
    remove_session
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
                
            prompt = (
                "You are an AI assistant answering questions based on provided document context.\n"
                "INSTRUCTION: Answer the question accurately using ONLY the provided document content.\n"
                "IMPORTANT: Respond in the same language as the user's Question (e.g., if asked in Hindi, respond in Hindi).\n"
                "DO NOT announce or write the name of the language in your response.\n\n"
            )
            for f in matched_files:
                prompt += f"FILE: {f['filename']}\n"
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