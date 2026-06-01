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
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from mssql_connector import connect_mssql
from mssql_schema_reader import get_all_tables, get_selected_schema_text
from mssql_sql_generator import generate_tsql, generate_answer_summary
from mssql_executor import validate_tsql, execute_tsql
from ollama_client import list_ollama_models
from audit_logger import log_query   # see audit_logger.py

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
    server: str
    database: str
    auth_mode: str
    username: Optional[str] = None
    password: Optional[str] = None
    driver: Optional[str] = None
    tables: list[str]                 # ["dbo.Orders", "dbo.Customers"]


class AskRequest(BaseModel):
    server: str
    database: str
    auth_mode: str
    username: Optional[str] = None
    password: Optional[str] = None
    driver: Optional[str] = None
    tables: list[str]
    question: str
    model: Optional[str] = None      # Ollama model name; omit for default


class ConnectResponse(BaseModel):
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
        table_labels = [f"{s}.{t}" for s, t in tables]
        return ConnectResponse(
            status="connected",
            database=req.database,
            table_count=len(tables),
            tables=table_labels,
        )
    finally:
        conn.close()


@app.post("/schema", response_model=SchemaResponse)
def schema(req: SchemaRequest, payload: dict = Depends(verify_token)):
    if not req.tables:
        raise HTTPException(status_code=400, detail="Provide at least one table.")
    conn = _get_conn(req)
    try:
        selected = [_parse_table(t) for t in req.tables]
        schema_text = get_selected_schema_text(conn, selected)
        return SchemaResponse(schema_text=schema_text)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        conn.close()


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, payload: dict = Depends(verify_token)):
    if not req.tables:
        raise HTTPException(status_code=400, detail="Provide at least one table.")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    conn = _get_conn(req)
    user_id = payload.get("sub", "unknown")

    try:
        selected = [_parse_table(t) for t in req.tables]
        schema_text = get_selected_schema_text(conn, selected)

        # Generate T-SQL
        sql = generate_tsql(req.question, schema_text, model=req.model)

        # Safety check
        is_safe, reason = validate_tsql(sql)
        if not is_safe:
            logger.warning("Blocked query from user=%s: %s", user_id, reason)
            raise HTTPException(status_code=400, detail=f"Unsafe query blocked: {reason}")

        # Execute
        columns, rows = execute_tsql(conn, sql)

        # AI summary
        answer = generate_answer_summary(
            req.question, sql, columns, rows, model=req.model
        )

        # Audit log
        log_query(
            user_id=user_id,
            question=req.question,
            sql=sql,
            row_count=len(rows),
            tables=req.tables,
            database=req.database,
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
        logger.error("Error processing ask request: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        conn.close()
