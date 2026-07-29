import pandas as pd
import streamlit as st

from db_connector import connect_db
from schema_reader import get_database_schema
from sql_generator import generate_sql
from sql_executor import (
    validate_sql,
    execute_sql
)
from history_manager import (
    init_db,
    save_chat
)

init_db()

st.set_page_config(
    page_title="Database AI Assistant",
    layout="wide"
)

st.title("Database AI Assistant")

# ==========================
# Database Connection
# ==========================

host = st.text_input(
    "Host",
    value="localhost"
)

port = st.number_input(
    "Port",
    value=3306
)

username = st.text_input(
    "Username"
)

password = st.text_input(
    "Password",
    type="password"
)

database = st.text_input(
    "Database Name"
)

if st.button("Connect Database"):

    try:

        conn = connect_db(
            host,
            port,
            username,
            password,
            database
        )

        schema = get_database_schema(
            conn
        )

        st.session_state["conn"] = conn
        st.session_state["schema"] = schema

        st.success(
            "Connected Successfully"
        )

        st.subheader(
            "Database Schema"
        )

        st.text_area(
            "Schema",
            schema,
            height=300
        )

    except Exception as e:

        st.error(
            f"Database Connection Error:\n{str(e)}"
        )

# ==========================
# Ask Question
# ==========================

st.divider()

question = st.text_area(
    "Ask Question"
)

if st.button("Ask"):

    if "conn" not in st.session_state:

        st.error(
            "Please connect database first."
        )

        st.stop()

    if not question.strip():

        st.error(
            "Please enter a question."
        )

        st.stop()

    try:

        schema = st.session_state["schema"]

        with st.spinner(
            "Generating SQL..."
        ):

            sql = generate_sql(
                question,
                schema
            )

        # ==========================
        # Show Generated SQL
        # ==========================

        st.subheader(
            "Generated SQL"
        )

        st.code(
            sql,
            language="sql"
        )

        print("\nGenerated SQL:")
        print(sql)

        # ==========================
        # Validate SQL
        # ==========================

        if not validate_sql(sql):

            st.error(
                "Unsafe SQL Detected. Query execution blocked."
            )

            st.stop()

        # ==========================
        # Execute SQL
        # ==========================

        with st.spinner(
            "Executing Query..."
        ):

            columns, rows = execute_sql(
                st.session_state["conn"],
                sql
            )

        # ==========================
        # Display Result
        # ==========================

        st.subheader(
            "Result"
        )

        if rows:

            df = pd.DataFrame(
                rows,
                columns=columns
            )

            st.dataframe(
                df,
                width="stretch"
            )

            save_chat(
                question,
                sql,
                str(rows)
            )

            st.success(
                f"{len(rows)} record(s) found."
            )

        else:

            st.warning(
                "No records found."
            )

    except Exception as e:

        st.error(
            f"Query Execution Error:\n{str(e)}"
        )