def validate_sql(sql):

    sql_lower = sql.lower()

    blocked = [
        "delete",
        "update",
        "insert",
        "drop",
        "truncate",
        "alter",
        "create"
    ]

    for word in blocked:

        if word in sql_lower:
            return False

    return True


def execute_sql(
        connection,
        sql):

    cursor = connection.cursor()

    cursor.execute(sql)

    columns = []

    if cursor.description:

        columns = [
            col[0]
            for col in cursor.description
        ]

    rows = cursor.fetchall()

    cursor.close()

    return columns, rows