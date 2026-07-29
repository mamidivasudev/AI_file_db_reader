# MSSQL AI Assistant — REST API Reference

Base URL: `http://your-server:8000`

All endpoints except `/health` require a **Bearer JWT token** in the `Authorization` header.

**Database Connection Details**

| Field | Value |
|---|---|
| Server | `192.168.1.19` |
| Database | `Hims_Zrams` |
| Auth Mode | SQL Server Authentication |
| Username | `sa` |
| Password | `user` |

---

## Sequential Usage Flow

```
1. /health      → confirm service is running
2. /models      → pick an Ollama model
3. /connect     → verify DB credentials, get table list
4. /schema      → inspect schema for selected tables
5. /ask         → ask a natural-language question, get SQL + results + AI answer
```

---

## 1. Health Check

Confirm the API service is alive. No authentication required.

**Request**
```http
GET /health
```

**Response `200 OK`**
```json
{
  "status": "ok",
  "timestamp": "2026-06-01T08:30:00.000000+00:00"
}
```

---

## 2. List Ollama Models

Returns all locally installed Ollama models available for SQL generation.

**Request**
```http
GET /models
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Response `200 OK`**
```json
{
  "models": [
    "llama3:latest",
    "mistral:latest",
    "qwen2.5-coder:7b"
  ]
}
```

> Use any model name from this list in the `model` field of `/ask`.

---

## 3. Connect to Database

Tests the database connection and returns all available base tables.

**Request**
```http
POST /connect
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: application/json
```

```json
{
  "server": "192.168.1.19",
  "database": "Hims_Zrams",
  "auth_mode": "SQL Server Authentication",
  "username": "sa",
  "password": "user",
  "driver": "ODBC Driver 17 for SQL Server"
}
```

**Response `200 OK`**
```json
{
  "status": "connected",
  "database": "Hims_Zrams",
  "table_count": 1258,
  "tables": [
    "dbo.SysRoadSum",
    "dbo.RoadSegments",
    "dbo.Districts",
    "..."
  ]
}
```

**Response `503 Service Unavailable`** *(wrong credentials)*
```json
{
  "detail": "DB connection failed: [HY000] [Microsoft][ODBC SQL Server Driver] Login failed for user 'sa'."
}
```

---

## 4. Get Schema

Returns the full schema (columns, data types, PKs, FKs) for selected tables — useful for confirming what context the AI will use before asking a question.

**Request**
```http
POST /schema
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: application/json
```

```json
{
  "server": "192.168.1.19",
  "database": "Hims_Zrams",
  "auth_mode": "SQL Server Authentication",
  "username": "sa",
  "password": "user",
  "tables": [
    "dbo.SysRoadSum"
  ]
}
```

**Response `200 OK`**
```json
{
  "schema_text": "TABLE: [dbo].[SysRoadSum]\n  RoadID int  [PK, NOT NULL]\n  RoadName nvarchar(255)  [NOT NULL]\n  DistrictID int  [FK→[dbo].[Districts].DistrictID, NOT NULL]\n  LengthKm decimal  [NOT NULL]\n  SurfaceType nvarchar(50)\n  Condition nvarchar(20)\n  LastInspected date"
}
```

**Response `400 Bad Request`** *(wrong table format — missing schema prefix)*
```json
{
  "detail": "Table 'SysRoadSum' must be in 'schema.table' format."
}
```

---

## 5. Ask a Question

The core endpoint. Accepts a natural-language question, generates T-SQL, executes it against `Hims_Zrams`, and returns the results plus an AI-written plain-English answer.

**Request**
```http
POST /ask
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: application/json
```

```json
{
  "server": "192.168.1.19",
  "database": "Hims_Zrams",
  "auth_mode": "SQL Server Authentication",
  "username": "sa",
  "password": "user",
  "tables": [
    "dbo.SysRoadSum"
  ],
  "question": "How many roads are there in total?",
  "model": "llama3:latest"
}
```

**Response `200 OK`**
```json
{
  "question": "How many roads are there in total?",
  "sql": "SELECT COUNT(*) AS TotalRoads FROM [dbo].[SysRoadSum];",
  "columns": ["TotalRoads"],
  "rows": [
    [3847]
  ],
  "row_count": 1,
  "answer": "There are 3,847 roads recorded in the Hims_Zrams database."
}
```

**Response `400 Bad Request`** *(unsafe query blocked)*
```json
{
  "detail": "Unsafe query blocked: Blocked keyword detected: 'drop'"
}
```

**Response `401 Unauthorized`** *(missing or expired token)*
```json
{
  "detail": "Invalid or expired token: Signature verification failed."
}
```

**Response `500 Internal Server Error`** *(query execution error)*
```json
{
  "detail": "Invalid column name 'RoadNam'."
}
```

---

## Request Fields Reference

| Field | Type | Required | Description |
|---|---|---|---|
| `server` | string | Yes | `192.168.1.19` |
| `database` | string | Yes | `Hims_Zrams` |
| `auth_mode` | string | Yes | `"SQL Server Authentication"` |
| `username` | string | Yes | `sa` |
| `password` | string | Yes | `user` |
| `driver` | string | No | ODBC driver name; auto-detected if omitted |
| `tables` | string[] | Yes | Table names in `schema.table` format, e.g. `"dbo.SysRoadSum"` |
| `question` | string | Yes (`/ask`) | Natural-language question about the data |
| `model` | string | No | Ollama model name from `/models`; uses default if omitted |

---

## Authentication

All protected endpoints expect an **HS256 JWT** in the `Authorization` header.

```
Authorization: Bearer <token>
```

Generate a token for testing (Python):

```python
from jose import jwt
import datetime

token = jwt.encode(
    {
        "sub": "sa",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    },
    key="your-secret-key",
    algorithm="HS256"
)
print(token)
```

Set the secret on the server:
```powershell
$env:SECRET_KEY = "your-secret-key"
```

---

## Running the Server

```powershell
# Install dependencies
pip install -r requirements.txt

# Start (development)
uvicorn fastapi_app:app --host 0.0.0.0 --port 8000

# Start (production — 4 workers)
gunicorn fastapi_app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

Interactive API docs (Swagger UI) available at: `http://your-server:8000/docs`

---

## Audit Log Table DDL

Run once on `Hims_Zrams` (or a dedicated audit database) before starting the server:

```sql
CREATE TABLE dbo.AiQueryAudit (
    id            BIGINT IDENTITY PRIMARY KEY,
    user_id       NVARCHAR(256)  NOT NULL,
    question      NVARCHAR(MAX)  NOT NULL,
    sql_query     NVARCHAR(MAX)  NOT NULL,
    tables_used   NVARCHAR(MAX)  NOT NULL,
    database_name NVARCHAR(256)  NOT NULL,
    row_count     INT            NOT NULL DEFAULT 0,
    created_at    DATETIME2      NOT NULL DEFAULT SYSUTCDATETIME()
);
```

Set audit DB env vars:
```powershell
$env:AUDIT_SERVER   = "192.168.1.19"
$env:AUDIT_DATABASE = "Hims_Zrams"
$env:AUDIT_UID      = "sa"
$env:AUDIT_PWD      = "user"
```
