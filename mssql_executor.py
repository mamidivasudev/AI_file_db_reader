import re

_BLOCKED_KEYWORDS = [
    r"\bdelete\b",
    r"\bupdate\b",
    r"\binsert\b",
    r"\bdrop\b",
    r"\btruncate\b",
    r"\balter\b",
    r"\bcreate\b",
    r"\bexec\b",
    r"\bexecute\b",
    r"\bxp_\w+",        # extended stored procs
    r"\bsp_\w+",        # system stored procs
    r"\bshutdown\b",
]

_BLOCKED_RE = re.compile(
    "|".join(_BLOCKED_KEYWORDS),
    re.IGNORECASE
)


def validate_tsql(sql):
    """Return (is_safe, reason). Blocks any DML/DDL/exec patterns."""
    stripped = sql.strip().lower()

    if not stripped.startswith("select"):
        return False, "Query must start with SELECT."

    match = _BLOCKED_RE.search(sql)
    if match:
        return False, f"Blocked keyword detected: '{match.group()}'"

    return True, ""


def execute_tsql(conn, sql):
    """Execute a SELECT query and return (columns, rows)."""
    cursor = conn.cursor()
    cursor.execute(sql)

    columns = []
    if cursor.description:
        columns = [col[0] for col in cursor.description]

    rows = cursor.fetchall()
    # Convert pyodbc Row objects to plain tuples
    rows = [tuple(row) for row in rows]

    cursor.close()
    return columns, rows
