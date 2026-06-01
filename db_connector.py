import mysql.connector


def connect_db(
        host,
        port,
        username,
        password,
        database):

    conn = mysql.connector.connect(
        host=host,
        port=port,
        user=username,
        password=password,
        database=database
    )

    return conn