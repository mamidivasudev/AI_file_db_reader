import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "chat_history.db")
BUSINESS_RULES_FILE = os.path.join(BASE_DIR, "business_rules.json")
SUGGESTIONS_FILE = os.path.join(BASE_DIR, "saved_suggestions.json")

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


def get_chat_history():
    import os
    if not os.path.exists(DB_FILE):
        return []
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT question, sql_query, answer FROM chat_history ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        seen = set()
        unique_history = []
        for row in rows:
            q = row[0]
            if q and q.strip().lower() not in seen:
                seen.add(q.strip().lower())
                unique_history.append(row)
        return unique_history
    except Exception:
        return []

def clear_chat_history():
    import os
    if not os.path.exists(DB_FILE):
        return
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("DELETE FROM chat_history")
        conn.commit()
        conn.close()
    except Exception:
        pass





def get_business_rules(db_identifier):
    """Load custom business rules from a permanent json file for a specific db."""
    if not db_identifier:
        return ""
    if os.path.exists(BUSINESS_RULES_FILE):
        try:
            import json
            with open(BUSINESS_RULES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                target_key = db_identifier.strip().lower()
                for k, v in data.items():
                    if k.strip().lower() == target_key:
                        return v
                return ""
        except Exception:
            pass
    return ""


def save_business_rules(db_identifier, rules_text):
    """Save custom business rules to a permanent json file tied to a specific db."""
    if not db_identifier:
        return
    import json
    data = {}
    if os.path.exists(BUSINESS_RULES_FILE):
        try:
            with open(BUSINESS_RULES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
            
    data[db_identifier] = rules_text
    
    try:
        with open(BUSINESS_RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass



def get_user_suggestions(db_type, tables):
    """Load user suggestions for a specific db_type and table selection."""
    if not os.path.exists(SUGGESTIONS_FILE):
        return []
        
    try:
        import json
        with open(SUGGESTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        tables_str = ','.join(sorted([str(t) for t in tables]))
        target_key = f"{db_type}|{tables_str}".strip().lower()
        
        suggs = []
        for k, v in data.items():
            if k.strip().lower() == target_key:
                suggs = v
                break
        
        seen = set()
        unique_suggs = []
        for q in suggs:
            norm_q = q.strip().lower()
            if norm_q not in seen:
                seen.add(norm_q)
                unique_suggs.append(q)
        return unique_suggs
    except Exception:
        return []

def save_user_suggestion(db_type, tables, question):
    """Save a single user suggestion tied to the db_type and selected tables."""
    import json
    data = {}
    if os.path.exists(SUGGESTIONS_FILE):
        try:
            with open(SUGGESTIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
            
    tables_str = ','.join(sorted([str(t) for t in tables]))
    key = f"{db_type}|{tables_str}"
    if key not in data:
        data[key] = []
        
    question_clean = question.strip()
    if not any(q.strip().lower() == question_clean.lower() for q in data[key]):
        data[key].append(question_clean)
        
    try:
        with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass