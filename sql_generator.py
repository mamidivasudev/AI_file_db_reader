from ollama_client import ask_ollama
import re


def generate_sql(
        question,
        schema_text):

    prompt = f"""
You are a MySQL expert.

Database Schema:

{schema_text}

Rules:
1. Return ONLY SQL.
2. Do NOT use markdown.
3. Do NOT use ```sql.
4. Do NOT explain anything.
5. Output must start directly with SELECT.
6. Use SELECT statements only.
7. Never generate UPDATE.
8. Never generate DELETE.
9. Never generate INSERT.
10. Never generate DROP.
11. Never generate ALTER.

Question:

{question}
"""

    sql = ask_ollama(prompt)

    # Remove markdown blocks if model still returns them
    sql = sql.replace("```sql", "")
    sql = sql.replace("```", "")

    # Extract SELECT query if extra text is returned
    match = re.search(
        r"(SELECT[\s\S]*?;)",
        sql,
        re.IGNORECASE
    )

    if match:
        sql = match.group(1)

    sql = sql.strip()

    return sql