import sqlite3

DB_FILE = "chat_history.db"


def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT,
            sql_query TEXT,
            answer TEXT
        )
    """)

    conn.commit()
    conn.close()


def save_chat(
        question,
        sql_query,
        answer):

    conn = sqlite3.connect(DB_FILE)

    conn.execute(
        """
        INSERT INTO chat_history
        (
            question,
            sql_query,
            answer
        )
        VALUES (?, ?, ?)
        """,
        (
            question,
            sql_query,
            answer
        )
    )

    conn.commit()
    conn.close()