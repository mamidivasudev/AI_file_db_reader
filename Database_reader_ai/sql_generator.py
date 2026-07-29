"""SQL query generation utilities."""

import re
from ollama_client import ask_ollama


def generate_tsql(question, schema_text, business_rules="", model=None):
    rules_text = f"\nCustom Business Rules:\n{business_rules}\n" if business_rules.strip() else ""
    prompt = f"""You are a MS SQL (T-SQL) expert.

Database Schema:
{schema_text}
{rules_text}

Rules:
1. Return ONLY a valid T-SQL SELECT statement.
2. Do NOT use markdown code fences or ```sql.
3. Do NOT explain anything.
4. Output must start directly with SELECT.
5. Use SELECT statements only — never UPDATE, DELETE, INSERT, DROP, ALTER, TRUNCATE, EXEC, or EXECUTE.
6. Use square bracket quoting for table and column names: [SchemaName].[TableName].
7. Use TOP instead of LIMIT for row limiting.
8. For string patterns use LIKE with % wildcards.
9. For date functions use GETDATE(), DATEADD(), DATEDIFF(), FORMAT() — not MySQL syntax.

Question:
{question}
"""

    kwargs = {"model": model} if model else {}
    raw = ask_ollama(prompt, **kwargs)

    # Strip markdown fences if model still adds them
    raw = re.sub(r"```(?:sql)?", "", raw, flags=re.IGNORECASE)
    raw = raw.replace("```", "")

    # Extract the first SELECT…; block
    match = re.search(r"(SELECT[\s\S]*?)(;|$)", raw, re.IGNORECASE)
    if match:
        sql = match.group(1).strip()
        if match.group(2) == ";":
            sql += ";"
    else:
        sql = raw.strip()

    return sql


def generate_answer_summary(question, sql, columns, rows, model=None):
    """Ask Ollama to summarise query results in plain English."""
    if not rows:
        return "No records were returned for your question."

    # Format rows as a compact text table (cap at 50 rows to stay in context)
    preview_rows = rows[:50]
    header = " | ".join(str(c) for c in columns)
    separator = "-" * len(header)
    row_lines = "\n".join(
        " | ".join(str(v) for v in row) for row in preview_rows
    )
    table_text = f"{header}\n{separator}\n{row_lines}"

    if len(rows) > 50:
        table_text += f"\n... ({len(rows) - 50} more rows not shown)"

    prompt = f"""You are a helpful data analyst. The user asked:

"{question}"

The SQL query returned these results:

{table_text}

Write a clear, concise natural-language answer (2–4 sentences) that directly answers the question based on the data above. Do not repeat the SQL. Do not use bullet points."""

    kwargs = {"model": model} if model else {}
    return ask_ollama(prompt, **kwargs)
