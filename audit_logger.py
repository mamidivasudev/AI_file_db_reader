"""
Audit logger — writes every query to an MSSQL audit table.
Falls back to local file log if DB is unavailable (never silently swallows).

Table DDL (run once on your audit DB):

    CREATE TABLE dbo.AiQueryAudit (
        id          BIGINT IDENTITY PRIMARY KEY,
        user_id     NVARCHAR(256)  NOT NULL,
        question    NVARCHAR(MAX)  NOT NULL,
        sql_query   NVARCHAR(MAX)  NOT NULL,
        tables_used NVARCHAR(MAX)  NOT NULL,
        database_name NVARCHAR(256) NOT NULL,
        row_count   INT            NOT NULL DEFAULT 0,
        created_at  DATETIME2      NOT NULL DEFAULT SYSUTCDATETIME()
    );

Set env vars:
    AUDIT_SERVER   — SQL Server host
    AUDIT_DATABASE — audit database name
    AUDIT_DRIVER   — ODBC driver (default: ODBC Driver 17 for SQL Server)
Uses Windows Auth by default; set AUDIT_UID + AUDIT_PWD for SQL Auth.
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("audit")

_AUDIT_SERVER = os.environ.get("AUDIT_SERVER", "")
_AUDIT_DB = os.environ.get("AUDIT_DATABASE", "")
_AUDIT_DRIVER = os.environ.get("AUDIT_DRIVER", "ODBC Driver 17 for SQL Server")
_AUDIT_UID = os.environ.get("AUDIT_UID", "")
_AUDIT_PWD = os.environ.get("AUDIT_PWD", "")

_INSERT_SQL = """
INSERT INTO dbo.AiQueryAudit
    (user_id, question, sql_query, tables_used, database_name, row_count)
VALUES (?, ?, ?, ?, ?, ?)
"""


def _get_audit_conn():
    import pyodbc
    if _AUDIT_UID and _AUDIT_PWD:
        conn_str = (
            f"DRIVER={{{_AUDIT_DRIVER}}};"
            f"SERVER={_AUDIT_SERVER};"
            f"DATABASE={_AUDIT_DB};"
            f"UID={_AUDIT_UID};PWD={_AUDIT_PWD};"
            f"MARS_Connection=yes;"
        )
    else:
        conn_str = (
            f"DRIVER={{{_AUDIT_DRIVER}}};"
            f"SERVER={_AUDIT_SERVER};"
            f"DATABASE={_AUDIT_DB};"
            f"Trusted_Connection=yes;"
            f"MARS_Connection=yes;"
        )
    return pyodbc.connect(conn_str, timeout=5)


def log_query(
    user_id: str,
    question: str,
    sql: str,
    row_count: int,
    tables: list[str],
    database: str,
) -> None:
    tables_json = json.dumps(tables)

    # Try MSSQL audit table first
    if _AUDIT_SERVER and _AUDIT_DB:
        try:
            conn = _get_audit_conn()
            cursor = conn.cursor()
            cursor.execute(
                _INSERT_SQL,
                user_id, question, sql, tables_json, database, row_count,
            )
            conn.commit()
            conn.close()
            return
        except Exception as exc:
            logger.error("Audit DB write failed, falling back to file log: %s", exc)

    # Fallback: append to local audit.log
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "database": database,
        "tables": tables,
        "question": question,
        "sql": sql,
        "row_count": row_count,
    }
    with open("audit.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
