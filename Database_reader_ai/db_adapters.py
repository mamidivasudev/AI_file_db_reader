import re
import json
import sqlite3
from ollama_client import ask_ollama

# Re-use existing MSSQL logic
from mssql_connector import connect_mssql, get_available_drivers
from mssql_schema_reader import get_all_tables, get_selected_schema_text
from mssql_sql_generator import generate_tsql, generate_answer_summary
from mssql_executor import validate_tsql, execute_tsql

DATABASES = [
    "MS SQL",
    "MySQL",
    "PostgreSQL",
    "Oracle Database",
    "SQLite",
    "MongoDB",
    "Redis"
]

# Safe dynamic import helpers
def import_mysql():
    try:
        import mysql.connector
        return mysql.connector
    except ImportError:
        raise ImportError(
            "MySQL connector not found. Please run: pip install mysql-connector-python"
        )

def import_postgres():
    try:
        import psycopg2
        return psycopg2
    except ImportError:
        raise ImportError(
            "PostgreSQL connector not found. Please run: pip install psycopg2-binary"
        )

def import_oracle():
    try:
        import oracledb
        return oracledb
    except ImportError:
        raise ImportError(
            "Oracle Database connector not found. Please run: pip install oracledb"
        )

def import_mongodb():
    try:
        import pymongo
        return pymongo
    except ImportError:
        raise ImportError(
            "MongoDB driver not found. Please run: pip install pymongo"
        )

def import_redis():
    try:
        import redis
        return redis
    except ImportError:
        raise ImportError(
            "Redis driver not found. Please run: pip install redis"
        )


# ==========================================
# 1. Connection Functions
# ==========================================
def connect_db(db_type, params):
    """
    Connect to the target database with parameters.
    """
    if db_type == "MS SQL":
        return connect_mssql(
            server=params.get("server"),
            database=params.get("database"),
            auth_mode=params.get("auth_mode"),
            username=params.get("username"),
            password=params.get("password"),
            driver=params.get("driver")
        )
    elif db_type == "MySQL":
        mysql = import_mysql()
        return mysql.connect(
            host=params.get("host", "localhost"),
            port=int(params.get("port", 3306)),
            user=params.get("username", "root"),
            password=params.get("password", ""),
            database=params.get("database", ""),
            connect_timeout=10
        )
    elif db_type == "PostgreSQL":
        psycopg2 = import_postgres()
        return psycopg2.connect(
            host=params.get("host", "localhost"),
            port=int(params.get("port", 5432)),
            user=params.get("username", "postgres"),
            password=params.get("password", ""),
            database=params.get("database", ""),
            connect_timeout=10
        )
    elif db_type == "Oracle Database":
        oracledb = import_oracle()
        # Enable thin mode by default (doesn't require Oracle Instant Client)
        return oracledb.connect(
            user=params.get("username"),
            password=params.get("password"),
            host=params.get("host", "localhost"),
            port=int(params.get("port", 1521)),
            service_name=params.get("service_name")
        )
    elif db_type == "SQLite":
        return sqlite3.connect(params.get("db_path"), check_same_thread=False)
    elif db_type == "MongoDB":
        pymongo = import_mongodb()
        client = pymongo.MongoClient(params.get("uri", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=5000)
        # Verify connection
        client.admin.command('ping')
        return client[params.get("database")]
    elif db_type == "Redis":
        redis_lib = import_redis()
        r = redis_lib.Redis(
            host=params.get("host", "localhost"),
            port=int(params.get("port", 6379)),
            password=params.get("password") or None,
            db=int(params.get("db_index", 0)),
            socket_timeout=5,
            decode_responses=True
        )
        r.ping()
        return r
    else:
        raise ValueError(f"Unknown database type: {db_type}")


# ==========================================
# 2. Schema Browser Functions
# ==========================================
def get_db_tables(conn, db_type):
    """
    Get tables/collections/key spaces.
    Returns: list of (schema/namespace, table/collection/pattern) tuples.
    """
    if db_type == "MS SQL":
        return get_all_tables(conn)
    
    elif db_type == "MySQL":
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        rows = cursor.fetchall()
        cursor.close()
        return [("Default", row[0]) for row in rows]
        
    elif db_type == "PostgreSQL":
        cursor = conn.cursor()
        cursor.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
        """)
        rows = cursor.fetchall()
        cursor.close()
        return [(row[0], row[1]) for row in rows]
        
    elif db_type == "Oracle Database":
        cursor = conn.cursor()
        cursor.execute("""
            SELECT owner, table_name
            FROM all_tables
            WHERE owner NOT IN ('SYS', 'SYSTEM', 'OUTLN', 'APPQOSSYS', 'DVSYS', 'DVF', 'LBACSYS', 'MDSYS', 'ORDDATA', 'ORDSYS', 'SYSBACKUP', 'SYSDG', 'SYSKM', 'SYSRAC', 'WMSYS', 'XDB', 'XS$NULL')
            ORDER BY owner, table_name
        """)
        rows = cursor.fetchall()
        cursor.close()
        return [(row[0], row[1]) for row in rows]
        
    elif db_type == "SQLite":
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        rows = cursor.fetchall()
        cursor.close()
        return [("main", row[0]) for row in rows]
        
    elif db_type == "MongoDB":
        cols = conn.list_collection_names()
        return [("Default", c) for c in cols]
        
    elif db_type == "Redis":
        try:
            keys = conn.keys("*")
        except Exception:
            keys = []
        prefixes = set()
        for k in keys:
            if ":" in k:
                prefixes.add(k.split(":")[0] + ":*")
            else:
                prefixes.add(k)
        if not prefixes:
            return [("Default", "* (All Keys)")]
        return [("Default", p) for p in sorted(list(prefixes))]
        
    return []


def get_db_schema_text(conn, db_type, selected_items):
    """
    Get text schema representation of selected tables/collections/key spaces.
    selected_items: list of (schema/namespace, table/collection/pattern) tuples.
    """
    if db_type == "MS SQL":
        return get_selected_schema_text(conn, selected_items)
        
    elif db_type == "MySQL":
        schema_text = []
        cursor = conn.cursor()
        for _, table in selected_items:
            cursor.execute(f"DESCRIBE `{table}`")
            cols = cursor.fetchall()
            lines = [f"TABLE: {table}"]
            for col in cols:
                # col[0]=Field, col[1]=Type, col[2]=Null, col[3]=Key, col[4]=Default, col[5]=Extra
                flags = []
                if col[3] == "PRI":
                    flags.append("PK")
                if col[2] == "NO":
                    flags.append("NOT NULL")
                if col[4] is not None:
                    flags.append(f"DEFAULT={col[4]}")
                flag_str = f"  [{', '.join(flags)}]" if flags else ""
                lines.append(f"  {col[0]} {col[1].upper()}{flag_str}")
            schema_text.append("\n".join(lines))
        cursor.close()
        return "\n\n".join(schema_text)
        
    elif db_type == "PostgreSQL":
        schema_text = []
        cursor = conn.cursor()
        for schema, table in selected_items:
            # Columns
            cursor.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
            """, (schema, table))
            cols = cursor.fetchall()
            # Primary keys
            cursor.execute("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = %s AND tc.table_name = %s
            """, (schema, table))
            pks = set(row[0] for row in cursor.fetchall())
            
            lines = [f"TABLE: \"{schema}\".\"{table}\""]
            for col in cols:
                flags = []
                if col[0] in pks:
                    flags.append("PK")
                if col[2] == "NO":
                    flags.append("NOT NULL")
                if col[3] is not None:
                    flags.append(f"DEFAULT={col[3]}")
                flag_str = f"  [{', '.join(flags)}]" if flags else ""
                lines.append(f"  {col[0]} {col[1].upper()}{flag_str}")
            schema_text.append("\n".join(lines))
        cursor.close()
        return "\n\n".join(schema_text)
        
    elif db_type == "Oracle Database":
        schema_text = []
        cursor = conn.cursor()
        for owner, table in selected_items:
            # Columns
            cursor.execute("""
                SELECT column_name, data_type, nullable, data_default
                FROM all_tab_columns
                WHERE owner = :owner AND table_name = :table_name
                ORDER BY column_id
            """, owner=owner, table_name=table)
            cols = cursor.fetchall()
            # PKs
            cursor.execute("""
                SELECT cols.column_name
                FROM all_constraints cons
                JOIN all_cons_columns cols ON cons.constraint_name = cols.constraint_name AND cons.owner = cols.owner
                WHERE cons.constraint_type = 'P'
                  AND cons.owner = :owner AND cons.table_name = :table_name
            """, owner=owner, table_name=table)
            pks = set(row[0] for row in cursor.fetchall())
            
            lines = [f"TABLE: {owner}.{table}"]
            for col in cols:
                flags = []
                if col[0] in pks:
                    flags.append("PK")
                if col[2] == "N":
                    flags.append("NOT NULL")
                if col[3] is not None:
                    flags.append(f"DEFAULT={col[3]}")
                flag_str = f"  [{', '.join(flags)}]" if flags else ""
                lines.append(f"  {col[0]} {col[1]}{flag_str}")
            schema_text.append("\n".join(lines))
        cursor.close()
        return "\n\n".join(schema_text)
        
    elif db_type == "SQLite":
        schema_text = []
        cursor = conn.cursor()
        for _, table in selected_items:
            cursor.execute(f"PRAGMA table_info('{table}')")
            cols = cursor.fetchall()
            lines = [f"TABLE: {table}"]
            for col in cols:
                # col[0]=cid, col[1]=name, col[2]=type, col[3]=notnull, col[4]=dflt_value, col[5]=pk
                flags = []
                if col[5] > 0:
                    flags.append("PK")
                if col[3] == 1:
                    flags.append("NOT NULL")
                if col[4] is not None:
                    flags.append(f"DEFAULT={col[4]}")
                flag_str = f"  [{', '.join(flags)}]" if flags else ""
                lines.append(f"  {col[1]} {col[2].upper()}{flag_str}")
            schema_text.append("\n".join(lines))
        cursor.close()
        return "\n\n".join(schema_text)
        
    elif db_type == "MongoDB":
        schema_text = []
        for _, col_name in selected_items:
            col = conn[col_name]
            sample_docs = list(col.find().limit(3))
            fields = {}
            for doc in sample_docs:
                for k, v in doc.items():
                    fields[k] = type(v).__name__
            lines = [f"COLLECTION: {col_name}"]
            for field, type_name in fields.items():
                lines.append(f"  {field} ({type_name})")
            if not sample_docs:
                lines.append("  (Empty collection)")
            schema_text.append("\n".join(lines))
        return "\n\n".join(schema_text)
        
    elif db_type == "Redis":
        schema_text = []
        for _, pattern in selected_items:
            # If pattern is * (All Keys), use keys matching *
            keys = conn.keys(pattern if pattern != "* (All Keys)" else "*")
            sample_keys = keys[:10]  # sample at most 10 keys
            lines = [f"REDIS KEY PATTERN: {pattern}"]
            for key in sample_keys:
                ktype = conn.type(key)
                lines.append(f"  Key: {key} (type: {ktype})")
                # sample value
                try:
                    if ktype == "string":
                        val = conn.get(key)
                        lines.append(f"    Value preview: {str(val)[:100]}")
                    elif ktype == "hash":
                        val = conn.hkeys(key)
                        lines.append(f"    Fields: {', '.join(val[:5])}")
                    elif ktype == "list":
                        val = conn.lrange(key, 0, 4)
                        lines.append(f"    Elements: {val}")
                    elif ktype == "set":
                        val = list(conn.smembers(key))[:5]
                        lines.append(f"    Members: {val}")
                    elif ktype == "zset":
                        val = conn.zrange(key, 0, 4)
                        lines.append(f"    Members: {val}")
                except Exception as e:
                    lines.append(f"    Could not read value: {e}")
            if not keys:
                lines.append("  (No matching keys found in database)")
            schema_text.append("\n".join(lines))
        return "\n\n".join(schema_text)
        
    return ""


# ==========================================
# 3. Query Generation Functions
# ==========================================
def generate_db_query(db_type, question, schema_text, business_rules="", model=None):
    """
    Generate query (SQL, MongoDB JSON, Redis JSON) matching the target DB.
    """
    if db_type == "MS SQL":
        return generate_tsql(question, schema_text, business_rules, model)
        
    kwargs = {"model": model} if model else {}
    rules_text = f"\nCustom Business Rules:\n{business_rules}\n" if business_rules.strip() else ""
    
    if db_type == "MySQL":
        prompt = f"""You are a MySQL database expert.
Database Schema:
{schema_text}
{rules_text}
Rules:
1. Return ONLY a valid MySQL SELECT query.
2. Do NOT use markdown code fences or ```sql.
3. Do NOT explain anything.
4. Output must start directly with SELECT.
5. Use LIMIT for row limiting.
6. Use backticks for quoting table and column names if needed.
7. Use SELECT statements only — never UPDATE, DELETE, INSERT, DROP, ALTER, TRUNCATE, or CREATE.

Question:
{question}
"""
        raw = ask_ollama(prompt, **kwargs)
        raw = re.sub(r"```(?:sql)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
        match = re.search(r"(SELECT[\s\S]*?)(;|$)", raw, re.IGNORECASE)
        return (match.group(0).strip() if match else raw)
        
    elif db_type == "PostgreSQL":
        prompt = f"""You are a PostgreSQL database expert.
Database Schema:
{schema_text}
{rules_text}
Rules:
1. Return ONLY a valid PostgreSQL SELECT query.
2. Do NOT use markdown code fences or ```sql.
3. Do NOT explain anything.
4. Output must start directly with SELECT.
5. Use LIMIT for row limiting.
6. Use double quotes for quoting table and column names if case-sensitive, or keep them unquoted.
7. Use SELECT statements only — never UPDATE, DELETE, INSERT, DROP, ALTER, TRUNCATE, or CREATE.

Question:
{question}
"""
        raw = ask_ollama(prompt, **kwargs)
        raw = re.sub(r"```(?:sql)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
        match = re.search(r"(SELECT[\s\S]*?)(;|$)", raw, re.IGNORECASE)
        return (match.group(0).strip() if match else raw)
        
    elif db_type == "Oracle Database":
        prompt = f"""You are an Oracle Database SQL expert.
Database Schema:
{schema_text}
{rules_text}
Rules:
1. Return ONLY a valid Oracle SQL SELECT query.
2. Do NOT use markdown code fences or ```sql.
3. Do NOT explain anything.
4. Output must start directly with SELECT.
5. Use Oracle row limiting syntax (e.g., FETCH FIRST N ROWS ONLY or ROWNUM).
6. Use SELECT statements only — never UPDATE, DELETE, INSERT, DROP, ALTER, TRUNCATE, or CREATE.

Question:
{question}
"""
        raw = ask_ollama(prompt, **kwargs)
        raw = re.sub(r"```(?:sql)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
        match = re.search(r"(SELECT[\s\S]*?)(;|$)", raw, re.IGNORECASE)
        return (match.group(0).strip() if match else raw)
        
    elif db_type == "SQLite":
        prompt = f"""You are a SQLite database expert.
Database Schema:
{schema_text}
{rules_text}
Rules:
1. Return ONLY a valid SQLite SELECT query.
2. Do NOT use markdown code fences or ```sql.
3. Do NOT explain anything.
4. Output must start directly with SELECT.
5. Use LIMIT for row limiting.
6. Use SELECT statements only — never UPDATE, DELETE, INSERT, DROP, ALTER, TRUNCATE, or CREATE.

Question:
{question}
"""
        raw = ask_ollama(prompt, **kwargs)
        raw = re.sub(r"```(?:sql)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
        match = re.search(r"(SELECT[\s\S]*?)(;|$)", raw, re.IGNORECASE)
        return (match.group(0).strip() if match else raw)
        
    elif db_type == "MongoDB":
        prompt = f"""You are a MongoDB query assistant.
Database Schema (Sample Document Fields):
{schema_text}
{rules_text}
Rules:
1. Return ONLY a valid JSON object matching this specification:
   {{
     "collection": "collection_name",
     "operation": "find", // "find" or "aggregate" or "count_documents"
     "filter": {{}}, // query filter for find or count_documents (optional)
     "projection": {{}}, // fields to select (optional)
     "sort": [["field", 1]], // sort specification array of pairs (optional)
     "limit": 50, // row limit (optional, max 100)
     "pipeline": [] // aggregation pipeline array (required for "aggregate")
   }}
2. Do NOT use markdown code fences or ```json.
3. Do NOT explain anything.
4. Output must start directly with {{.
5. Generate read-only operations only.

Question:
{question}
"""
        raw = ask_ollama(prompt, **kwargs)
        raw = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
        # Find start { and end }
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end+1]
        return raw
        
    elif db_type == "Redis":
        prompt = f"""You are a Redis command query assistant.
Database Schema/Keyspace:
{schema_text}
{rules_text}
Rules:
1. Return ONLY a valid JSON object representing a Redis command to answer the question:
   {{
     "command": "redis_command", // e.g., "GET", "HGETALL", "SMEMBERS", "LRANGE", "ZRANGE", "KEYS", "TYPE"
     "args": ["arg1", "arg2", ...]
   }}
2. Do NOT use markdown code fences or ```json.
3. Do NOT explain anything.
4. Output must start directly with {{.
5. Never generate commands that modify or delete data (e.g., SET, DEL, FLUSHALL, FLUSHDB, EXPIRE, HDEL).

Question:
{question}
"""
        raw = ask_ollama(prompt, **kwargs)
        raw = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end+1]
        return raw
        
    return ""


# ==========================================
# 4. Query Validation Functions
# ==========================================
def validate_db_query(db_type, query):
    """
    Validate the query for safety. Return (is_safe, reason).
    """
    if db_type == "MS SQL":
        return validate_tsql(query)
        
    if db_type in ["MySQL", "PostgreSQL", "Oracle Database", "SQLite"]:
        stripped = query.strip().lower()
        if not stripped.startswith("select"):
            return False, "Query must start with SELECT."
            
        blocked = [
            r"\bdelete\b", r"\bupdate\b", r"\binsert\b", r"\bdrop\b",
            r"\btruncate\b", r"\balter\b", r"\bcreate\b", r"\bexec\b",
            r"\bexecute\b", r"\bshutdown\b", r"\bgrant\b", r"\brevoke\b"
        ]
        blocked_re = re.compile("|".join(blocked), re.IGNORECASE)
        match = blocked_re.search(query)
        if match:
            return False, f"Blocked keyword detected: '{match.group()}'"
        return True, ""
        
    elif db_type == "MongoDB":
        try:
            spec = json.loads(query)
        except Exception as e:
            return False, f"Invalid JSON generated: {e}"
            
        operation = spec.get("operation", "").lower()
        if operation not in ["find", "aggregate", "count_documents"]:
            return False, f"Blocked or invalid operation: '{operation}'. Only find, aggregate, and count_documents are allowed."
            
        # Check for any mutating operators in filter/pipeline
        query_str = json.dumps(spec).lower()
        blocked = ["$out", "$merge", "$writeconcern", "drop", "delete", "update", "insert"]
        for b in blocked:
            if b in query_str:
                return False, f"Blocked MongoDB keyword/operator detected: '{b}'"
        return True, ""
        
    elif db_type == "Redis":
        try:
            spec = json.loads(query)
        except Exception as e:
            return False, f"Invalid JSON generated: {e}"
            
        command = spec.get("command", "").upper()
        allowed = ["GET", "MGET", "HGET", "HMGET", "HGETALL", "KEYS", "SCAN", "SMEMBERS", 
                   "SISMEMBER", "SUNION", "SINTER", "SDIFF", "LRANGE", "LINDEX", "LLEN", 
                   "ZRANGE", "ZRANGEBYSCORE", "ZCARD", "ZSCORE", "TYPE", "TTL", "EXISTS"]
        if command not in allowed:
            return False, f"Blocked or invalid Redis command: '{command}'"
        return True, ""
        
    return False, "Unknown database validation logic."


# ==========================================
# 5. Query Execution Functions
# ==========================================
def execute_db_query(conn, db_type, query):
    """
    Execute query against target connection.
    Returns: (columns_list, rows_list)
    """
    if db_type == "MS SQL":
        return execute_tsql(conn, query)
        
    elif db_type in ["MySQL", "PostgreSQL", "Oracle Database", "SQLite"]:
        cursor = conn.cursor()
        cursor.execute(query)
        
        columns = []
        if cursor.description:
            columns = [col[0] for col in cursor.description]
            
        rows = cursor.fetchall()
        # Convert any row objects/tuples into standard lists
        rows = [list(row) for row in rows]
        
        # Commit if needed, though SELECT shouldn't mutate. 
        # PostgreSQL/MySQL cursor close is clean.
        cursor.close()
        return columns, rows
        
    elif db_type == "MongoDB":
        spec = json.loads(query)
        collection_name = spec.get("collection")
        operation = spec.get("operation", "find")
        filter_dict = spec.get("filter", {})
        projection = spec.get("projection", None)
        sort = spec.get("sort", None)
        limit = min(spec.get("limit", 50), 100)
        pipeline = spec.get("pipeline", [])
        
        collection = conn[collection_name]
        
        if operation == "find":
            cursor = collection.find(filter_dict, projection)
            if sort:
                # Sort can be given as list of lists e.g. [["field", 1]]
                cursor = cursor.sort(sort)
            cursor = cursor.limit(limit)
            results = list(cursor)
        elif operation == "aggregate":
            # Add safety limit to aggregation if possible
            if pipeline and "$limit" not in [list(step.keys())[0] for step in pipeline]:
                pipeline.append({"$limit": limit})
            results = list(collection.aggregate(pipeline))
        elif operation == "count_documents":
            count = collection.count_documents(filter_dict)
            results = [{"count": count}]
        else:
            raise ValueError(f"Unsupported MongoDB operation: {operation}")
            
        if not results:
            return [], []
            
        # In MongoDB, keys across documents can vary. Collect all unique keys.
        keys = set()
        for doc in results:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])  # ObjectId to str
            keys.update(doc.keys())
            
        columns = sorted(list(keys))
        rows = []
        for doc in results:
            rows.append([doc.get(c, None) for c in columns])
        return columns, rows
        
    elif db_type == "Redis":
        spec = json.loads(query)
        command = spec.get("command", "").upper()
        args = spec.get("args", [])
        
        # Call redis method dynamically
        method = getattr(conn, command.lower(), None)
        if not method:
            raise ValueError(f"Redis command not supported: {command}")
            
        result = method(*args)
        
        # Structure the results based on type to present as Columns and Rows
        if command in ["GET", "TYPE", "TTL", "EXISTS"]:
            columns = ["Property", "Value"]
            key_name = args[0] if args else "result"
            rows = [[key_name, str(result)]]
        elif command in ["MGET"]:
            columns = ["Key", "Value"]
            rows = [[args[i], str(result[i])] for i in range(len(args))]
        elif command in ["HGETALL"]:
            columns = ["Field", "Value"]
            rows = [[k, str(v)] for k, v in result.items()] if isinstance(result, dict) else []
        elif command in ["HGET"]:
            columns = ["Field", "Value"]
            rows = [[args[1], str(result)]]
        elif command in ["KEYS", "SCAN"]:
            columns = ["Key"]
            # KEYS returns list of keys
            key_list = result[1] if isinstance(result, tuple) else result
            rows = [[str(k)] for k in key_list]
        elif command in ["SMEMBERS", "SUNION", "SINTER", "SDIFF"]:
            columns = ["Member"]
            rows = [[str(m)] for m in result]
        elif command in ["LRANGE", "ZRANGE", "ZRANGEBYSCORE"]:
            columns = ["Index/Score", "Value"]
            # Redis-py might return list of strings or list of tuples if withscores=True
            rows = []
            for idx, item in enumerate(result):
                if isinstance(item, tuple):
                    rows.append([str(item[1]), str(item[0])])
                else:
                    rows.append([idx, str(item)])
        else:
            columns = ["Result"]
            rows = [[str(result)]]
            
        return columns, rows
        
    return [], []


# ==========================================
# 6. Unified Answer Summarizer
# ==========================================
def generate_db_answer_summary(db_type, question, query, columns, rows, model=None):
    """
    Format connection results and ask Ollama to explain them.
    """
    if db_type == "MS SQL":
        return generate_answer_summary(question, query, columns, rows, model)
        
    if not rows:
        return "No records were returned for your query."
        
    # Cap rows for context
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

The query executed against the database ({db_type}) was:
{query}

The query returned the following results:
{table_text}

Write a clear, concise natural-language answer (2–4 sentences) that directly answers the question based on the data above. Do not repeat the query itself. Do not use bullet points."""

    kwargs = {"model": model} if model else {}
    return ask_ollama(prompt, **kwargs)


# ==========================================
# 7. Unified Metadata Retrieval
# ==========================================
def get_db_table_metadata(conn, db_type, schema_name, table_name):
    """
    Get columns, primary keys, and foreign keys for the table/collection/key-pattern.
    Returns a dict:
    {
        "columns": [{"name": str, "type": str, "max_length": int/None, "nullable": str, "default": str}],
        "pks": list of PK column names,
        "fks": [{"column": str, "ref_schema": str, "ref_table": str, "ref_column": str}]
    }
    """
    if db_type == "MS SQL":
        from mssql_schema_reader import get_table_columns, get_primary_keys, get_foreign_keys
        cols = get_table_columns(conn, schema_name, table_name)
        pks = get_primary_keys(conn, schema_name, table_name)
        fks = get_foreign_keys(conn, schema_name, table_name)
        return {
            "columns": cols,
            "pks": pks,
            "fks": fks
        }

    elif db_type == "MySQL":
        cursor = conn.cursor()
        # Retrieve columns
        cursor.execute(f"DESCRIBE `{table_name}`")
        rows = cursor.fetchall()
        cols = []
        pks = []
        for r in rows:
            # col[0]=Field, col[1]=Type, col[2]=Null, col[3]=Key, col[4]=Default, col[5]=Extra
            name = r[0]
            col_type = r[1].decode() if isinstance(r[1], bytes) else r[1]
            nullable = r[2]
            default = r[4] if r[4] is not None else ""
            cols.append({
                "name": name,
                "type": col_type,
                "max_length": None,
                "nullable": nullable,
                "default": default
            })
            if r[3] == "PRI":
                pks.append(name)
        
        # Retrieve FKs
        fks = []
        try:
            cursor.execute("""
                SELECT COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                WHERE TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL
            """, (table_name,))
            fk_rows = cursor.fetchall()
            for fk_row in fk_rows:
                fks.append({
                    "column": fk_row[0],
                    "ref_schema": fk_row[1],
                    "ref_table": fk_row[2],
                    "ref_column": fk_row[3]
                })
        except Exception:
            pass  # Fallback gracefully
            
        cursor.close()
        return {"columns": cols, "pks": pks, "fks": fks}

    elif db_type == "PostgreSQL":
        cursor = conn.cursor()
        cursor.execute("""
            SELECT column_name, data_type, is_nullable, column_default, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """, (schema_name, table_name))
        rows = cursor.fetchall()
        cols = []
        for r in rows:
            cols.append({
                "name": r[0],
                "type": r[1],
                "max_length": r[4],
                "nullable": r[2],
                "default": str(r[3]) if r[3] is not None else ""
            })
            
        cursor.execute("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s AND tc.table_name = %s
        """, (schema_name, table_name))
        pks = [row[0] for row in cursor.fetchall()]
        
        fks = []
        try:
            cursor.execute("""
                SELECT
                    kcu.column_name,
                    ccu.table_schema AS ref_schema,
                    ccu.table_name AS ref_table,
                    ccu.column_name AS ref_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.referential_constraints rc
                  ON tc.constraint_name = rc.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                  ON rc.unique_constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = %s AND tc.table_name = %s
            """, (schema_name, table_name))
            for r in cursor.fetchall():
                fks.append({
                    "column": r[0],
                    "ref_schema": r[1],
                    "ref_table": r[2],
                    "ref_column": r[3]
                })
        except Exception:
            pass
            
        cursor.close()
        return {"columns": cols, "pks": pks, "fks": fks}

    elif db_type == "Oracle Database":
        cursor = conn.cursor()
        cursor.execute("""
            SELECT column_name, data_type, nullable, data_default, data_length
            FROM all_tab_columns
            WHERE owner = :owner AND table_name = :table_name
            ORDER BY column_id
        """, owner=schema_name, table_name=table_name)
        rows = cursor.fetchall()
        cols = []
        for r in rows:
            cols.append({
                "name": r[0],
                "type": r[1],
                "max_length": r[4],
                "nullable": "YES" if r[2] == "Y" else "NO",
                "default": str(r[3]) if r[3] is not None else ""
            })
            
        cursor.execute("""
            SELECT cols.column_name
            FROM all_constraints cons
            JOIN all_cons_columns cols ON cons.constraint_name = cols.constraint_name AND cons.owner = cols.owner
            WHERE cons.constraint_type = 'P'
              AND cons.owner = :owner AND cons.table_name = :table_name
        """, owner=schema_name, table_name=table_name)
        pks = [row[0] for row in cursor.fetchall()]
        
        fks = []
        try:
            cursor.execute("""
                SELECT 
                    a.column_name AS local_column,
                    c_pk.owner AS ref_owner,
                    c_pk.table_name AS ref_table,
                    b.column_name AS ref_column
                FROM all_cons_columns a
                JOIN all_constraints c ON a.constraint_name = c.constraint_name AND a.owner = c.owner
                JOIN all_constraints c_pk ON c.r_constraint_name = c_pk.constraint_name AND c.r_owner = c_pk.owner
                JOIN all_cons_columns b ON c_pk.constraint_name = b.constraint_name AND c_pk.owner = b.owner
                WHERE c.constraint_type = 'R'
                  AND c.owner = :owner AND c.table_name = :table_name
            """, owner=schema_name, table_name=table_name)
            for r in cursor.fetchall():
                fks.append({
                    "column": r[0],
                    "ref_schema": r[1],
                    "ref_table": r[2],
                    "ref_column": r[3]
                })
        except Exception:
            pass
            
        cursor.close()
        return {"columns": cols, "pks": pks, "fks": fks}

    elif db_type == "SQLite":
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info('{table_name}')")
        rows = cursor.fetchall()
        cols = []
        pks = []
        for r in rows:
            # r[0]=cid, r[1]=name, r[2]=type, r[3]=notnull, r[4]=dflt_value, r[5]=pk
            cols.append({
                "name": r[1],
                "type": r[2],
                "max_length": None,
                "nullable": "NO" if r[3] == 1 else "YES",
                "default": str(r[4]) if r[4] is not None else ""
            })
            if r[5] > 0:
                pks.append(r[1])
                
        # Foreign Keys
        fks = []
        try:
            cursor.execute(f"PRAGMA foreign_key_list('{table_name}')")
            fk_rows = cursor.fetchall()
            for fk_row in fk_rows:
                # fk_row[3] = child_table (ref_table), fk_row[4] = from (local col), fk_row[5] = to (ref col)
                fks.append({
                    "column": fk_row[3],
                    "ref_schema": "main",
                    "ref_table": fk_row[2],
                    "ref_column": fk_row[4]
                })
        except Exception:
            pass
            
        cursor.close()
        return {"columns": cols, "pks": pks, "fks": fks}

    elif db_type == "MongoDB":
        col = conn[table_name]
        sample_docs = list(col.find().limit(5))
        fields = {}
        for doc in sample_docs:
            for k, v in doc.items():
                fields[k] = type(v).__name__
        cols = []
        for field, type_name in fields.items():
            cols.append({
                "name": field,
                "type": type_name.upper(),
                "max_length": None,
                "nullable": "YES",
                "default": ""
            })
        return {
            "columns": cols,
            "pks": ["_id"] if "_id" in fields else [],
            "fks": []
        }

    elif db_type == "Redis":
        # Table name is the key pattern. Scan matching keys.
        keys = conn.keys(table_name if table_name != "* (All Keys)" else "*")
        sample_keys = keys[:10]
        cols = []
        for key in sample_keys:
            try:
                ktype = conn.type(key)
                ttl = conn.ttl(key)
                cols.append({
                    "name": key,
                    "type": ktype.upper(),
                    "max_length": None,
                    "nullable": "NO",
                    "default": f"TTL: {ttl}s"
                })
            except Exception:
                pass
        if not cols:
            cols.append({
                "name": "(No keys found or matched)",
                "type": "NONE",
                "max_length": None,
                "nullable": "YES",
                "default": ""
            })
        return {
            "columns": cols,
            "pks": [],
            "fks": []
        }

    return {"columns": [], "pks": [], "fks": []}


# ==========================================
# 8. Question Suggestions
# ==========================================
def generate_suggested_questions(schema_text, model=None):
    """
    Generate 3 to 5 sample questions based on the selected schema.
    Returns a list of strings.
    """
    if not schema_text or schema_text.strip() == "":
        return []

    prompt = f"""You are a helpful data analyst. I will provide you with a database schema.
Please generate 3 to 5 realistic, analytical questions that a business user might ask based on this schema.

Rules:
1. Return ONLY the questions, one per line.
2. Do not number the questions.
3. Do not include any explanations or introductions like "Here are the questions:".
4. Ensure the questions are relevant to the provided tables and columns.

Database Schema:
{schema_text}
"""

    kwargs = {"model": model} if model else {}
    raw_response = ask_ollama(prompt, **kwargs)
    
    # Parse the response into a list of non-empty lines
    questions = []
    for line in raw_response.splitlines():
        cleaned_line = line.strip()
        # Remove markdown list bullets or numbers if the LLM adds them despite instructions
        cleaned_line = re.sub(r"^(\d+\.|\*|-)\s+", "", cleaned_line).strip()
        if cleaned_line and not cleaned_line.startswith("```"):
            questions.append(cleaned_line)
            
    # Return up to 5 questions
    return questions[:5]
