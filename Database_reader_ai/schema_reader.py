def get_database_schema(connection):

    cursor = connection.cursor()

    cursor.execute("SHOW TABLES")

    tables = cursor.fetchall()

    schema_text = ""

    for table in tables:

        table_name = table[0]

        schema_text += f"\n\nTABLE: {table_name}\n"

        cursor.execute(
            f"DESCRIBE {table_name}"
        )

        columns = cursor.fetchall()

        for col in columns:

            schema_text += (
                f"{col[0]} {col[1]}\n"
            )

    cursor.close()

    return schema_text