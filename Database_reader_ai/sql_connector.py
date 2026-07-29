"""SQL database connector utility functions."""

import pyodbc


def get_available_drivers():
    return [d for d in pyodbc.drivers() if "SQL Server" in d]


def connect_mssql(
        server,
        database,
        auth_mode,
        username=None,
        password=None,
        driver=None):

    if not driver:
        drivers = get_available_drivers()
        if not drivers:
            raise RuntimeError(
                "No SQL Server ODBC driver found. "
                "Install 'ODBC Driver 17 for SQL Server' or 'ODBC Driver 18 for SQL Server'."
            )
        driver = drivers[-1]

    if auth_mode == "Windows Authentication":
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"Trusted_Connection=yes;"
            f"MARS_Connection=yes;"
        )
    else:
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            f"MARS_Connection=yes;"
        )

    conn = pyodbc.connect(conn_str, timeout=10)
    return conn
