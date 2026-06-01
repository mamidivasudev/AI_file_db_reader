def get_all_tables(conn):
    """Return list of (schema, table_name) tuples from the database."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)
    tables = cursor.fetchall()
    cursor.close()
    return [(row[0], row[1]) for row in tables]


def get_primary_keys(conn, schema, table):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT kcu.COLUMN_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
            ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
            AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
            AND tc.TABLE_NAME = kcu.TABLE_NAME
        WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
          AND tc.TABLE_SCHEMA = ?
          AND tc.TABLE_NAME = ?
        ORDER BY kcu.ORDINAL_POSITION
    """, schema, table)
    rows = cursor.fetchall()
    cursor.close()
    return [row[0] for row in rows]


def get_foreign_keys(conn, schema, table):
    """Return list of dicts describing FK relationships for the table."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            kcu.COLUMN_NAME,
            ccu.TABLE_SCHEMA AS REF_SCHEMA,
            ccu.TABLE_NAME  AS REF_TABLE,
            ccu.COLUMN_NAME AS REF_COLUMN
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
            ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
            AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
            AND tc.TABLE_NAME = kcu.TABLE_NAME
        JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
            ON tc.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu
            ON rc.UNIQUE_CONSTRAINT_NAME = ccu.CONSTRAINT_NAME
        WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
          AND tc.TABLE_SCHEMA = ?
          AND tc.TABLE_NAME = ?
    """, schema, table)
    rows = cursor.fetchall()
    cursor.close()
    return [
        {
            "column": row[0],
            "ref_schema": row[1],
            "ref_table": row[2],
            "ref_column": row[3],
        }
        for row in rows
    ]


def get_table_columns(conn, schema, table):
    """Return list of dicts with column metadata."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            COLUMN_NAME,
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH,
            IS_NULLABLE,
            COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ?
          AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, schema, table)
    rows = cursor.fetchall()
    cursor.close()
    return [
        {
            "name": row[0],
            "type": row[1],
            "max_length": row[2],
            "nullable": row[3],
            "default": row[4],
        }
        for row in rows
    ]


def get_table_schema_text(conn, schema, table):
    """Build a compact schema string for one table (for AI prompt)."""
    columns = get_table_columns(conn, schema, table)
    pks = set(get_primary_keys(conn, schema, table))
    fks = {fk["column"]: fk for fk in get_foreign_keys(conn, schema, table)}

    lines = [f"TABLE: [{schema}].[{table}]"]
    for col in columns:
        type_str = col["type"].upper()
        if col["max_length"]:
            type_str += f"({col['max_length']})"

        flags = []
        if col["name"] in pks:
            flags.append("PK")
        if col["name"] in fks:
            fk = fks[col["name"]]
            flags.append(
                f"FK→[{fk['ref_schema']}].[{fk['ref_table']}].{fk['ref_column']}"
            )
        if col["nullable"] == "NO":
            flags.append("NOT NULL")
        if col["default"]:
            flags.append(f"DEFAULT={col['default']}")

        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {col['name']} {type_str}{flag_str}")

    return "\n".join(lines)


def get_selected_schema_text(conn, selected_tables):
    """
    selected_tables: list of (schema, table) tuples.
    Returns combined schema text for the AI prompt.
    """
    parts = []
    for schema, table in selected_tables:
        parts.append(get_table_schema_text(conn, schema, table))
    return "\n\n".join(parts)
