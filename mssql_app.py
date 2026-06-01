import pandas as pd
import streamlit as st

from mssql_connector import connect_mssql, get_available_drivers
from ollama_client import list_ollama_models
from mssql_schema_reader import (
    get_all_tables,
    get_table_columns,
    get_primary_keys,
    get_foreign_keys,
    get_selected_schema_text,
)
from mssql_sql_generator import generate_tsql, generate_answer_summary
from mssql_executor import validate_tsql, execute_tsql
from history_manager import init_db, save_chat

# ─────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────
init_db()

st.set_page_config(
    page_title="MSSQL AI Assistant",
    layout="wide",
    page_icon="🗄️",
)

# ─────────────────────────────────────────────
# Session state defaults
# ─────────────────────────────────────────────
for key, default in {
    "mssql_conn": None,
    "all_tables": [],
    "selected_tables": [],
    "chat_history": [],
    "table_multiselect": [],   # drives the multiselect widget via key
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────
# Sidebar — Connection
# ─────────────────────────────────────────────
with st.sidebar:
    st.title("🗄️ MSSQL AI Assistant")
    st.divider()

    st.subheader("🤖 Ollama Model")
    available_models = list_ollama_models()
    if available_models:
        selected_model = st.selectbox(
            "Model",
            options=available_models,
            label_visibility="collapsed",
        )
    else:
        st.warning("No Ollama models found. Run `ollama pull <model>` first.")
        selected_model = st.text_input("Model name", value="qwen2.5-coder:7b")

    st.divider()
    st.subheader("🔌 Connection")

    available_drivers = get_available_drivers()
    if available_drivers:
        driver = st.selectbox(
            "ODBC Driver",
            options=available_drivers,
            index=len(available_drivers) - 1,
        )
    else:
        st.error(
            "No SQL Server ODBC driver found.\n"
            "Install **ODBC Driver 17** or **18 for SQL Server**."
        )
        driver = None

    server = st.text_input("Server", placeholder="localhost or HOST\\INSTANCE")
    database = st.text_input("Database", placeholder="MyDatabase")

    auth_mode = st.radio(
        "Authentication",
        ["Windows Authentication", "SQL Server Authentication"],
        horizontal=True,
    )

    username = password = None
    if auth_mode == "SQL Server Authentication":
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

    connect_clicked = st.button(
        "Connect",
        use_container_width=True,
        type="primary",
        disabled=(driver is None),
    )

    if connect_clicked:
        if not server or not database:
            st.error("Please fill in Server and Database.")
        else:
            with st.spinner("Connecting…"):
                try:
                    conn = connect_mssql(
                        server=server,
                        database=database,
                        auth_mode=auth_mode,
                        username=username,
                        password=password,
                        driver=driver,
                    )
                    st.session_state["mssql_conn"] = conn
                    st.session_state["all_tables"] = get_all_tables(conn)
                    st.session_state["selected_tables"] = []
                    st.session_state["table_multiselect"] = []
                    st.session_state["chat_history"] = []
                    st.success("Connected!")
                except Exception as exc:
                    st.error(f"Connection failed:\n{exc}")

    # ── Table Selector (only when connected) ──
    if st.session_state["mssql_conn"] is not None:
        st.divider()
        st.subheader("📋 Select Tables")

        all_tables = st.session_state["all_tables"]
        table_labels = [f"{s}.{t}" for s, t in all_tables]

        if not table_labels:
            st.warning("No base tables found in this database.")
        else:
            st.caption(
                f"{len(table_labels)} table(s) available. "
                "Pick the ones relevant to your question."
            )

            col_a, col_b = st.columns(2)
            if col_a.button("All", use_container_width=True):
                st.session_state["table_multiselect"] = table_labels
            if col_b.button("None", use_container_width=True):
                st.session_state["table_multiselect"] = []

            # key= makes Streamlit read/write widget state from session_state directly
            # so All/None buttons above reliably control the selection without conflicts
            st.multiselect(
                "Tables",
                options=table_labels,
                key="table_multiselect",
                label_visibility="collapsed",
            )

            st.session_state["selected_tables"] = [
                (lbl.split(".", 1)[0], lbl.split(".", 1)[1])
                for lbl in st.session_state["table_multiselect"]
            ]

        st.divider()
        if st.button("🔌 Disconnect", use_container_width=True):
            try:
                st.session_state["mssql_conn"].close()
            except Exception:
                pass
            st.session_state["mssql_conn"] = None
            st.session_state["all_tables"] = []
            st.session_state["selected_tables"] = []
            st.session_state["table_multiselect"] = []
            st.session_state["chat_history"] = []
            st.rerun()

# ─────────────────────────────────────────────
# Main content
# ─────────────────────────────────────────────
if st.session_state["mssql_conn"] is None:
    # Landing page
    st.title("🗄️ MSSQL AI Assistant")
    st.markdown(
        """
        Connect to your **Microsoft SQL Server** database using the sidebar, then:

        1. **Browse** your tables and their full schema (columns, types, PKs, FKs)
        2. **Select** the tables relevant to your question
        3. **Ask** a natural-language question — the AI generates T-SQL, runs it, and explains the answer
        """
    )
    st.info("👈 Fill in the connection details in the sidebar to get started.")
    st.stop()

conn = st.session_state["mssql_conn"]
selected_tables = st.session_state["selected_tables"]

# ─────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────
tab_schema, tab_query = st.tabs(["📐 Schema Browser", "💬 Query Assistant"])

# ══════════════════════════════════════════════
# TAB 1 — Schema Browser
# ══════════════════════════════════════════════
with tab_schema:
    all_tables = st.session_state["all_tables"]

    if not all_tables:
        st.warning("No tables found.")
    else:
        browse_target = selected_tables if selected_tables else all_tables

        if selected_tables:
            st.info(
                f"Showing **{len(selected_tables)}** selected table(s). "
                "Deselect tables in the sidebar to browse all."
            )
        else:
            st.info(
                f"Showing all **{len(all_tables)}** table(s). "
                "Select tables in the sidebar to filter."
            )

        search = st.text_input(
            "🔍 Filter tables",
            placeholder="Type to filter by table name…",
        )

        for schema_name, table_name in browse_target:
            label = f"{schema_name}.{table_name}"
            if search and search.lower() not in label.lower():
                continue

            with st.expander(f"**{label}**", expanded=False):
                try:
                    columns = get_table_columns(conn, schema_name, table_name)
                    pks = set(get_primary_keys(conn, schema_name, table_name))
                    fks_list = get_foreign_keys(conn, schema_name, table_name)
                    fk_map = {fk["column"]: fk for fk in fks_list}

                    rows_data = []
                    for col in columns:
                        type_str = col["type"].upper()
                        if col["max_length"]:
                            type_str += f"({col['max_length']})"

                        badges = []
                        if col["name"] in pks:
                            badges.append("🔑 PK")
                        if col["name"] in fk_map:
                            fk = fk_map[col["name"]]
                            badges.append(
                                f"🔗 FK → {fk['ref_schema']}.{fk['ref_table']}.{fk['ref_column']}"
                            )

                        rows_data.append({
                            "Column": col["name"],
                            "Type": type_str,
                            "Nullable": col["nullable"],
                            "Default": col["default"] or "",
                            "Keys": "  ".join(badges),
                        })

                    df_schema = pd.DataFrame(rows_data)
                    st.dataframe(
                        df_schema,
                        use_container_width=True,
                        hide_index=True,
                    )

                    # FK summary
                    if fks_list:
                        st.markdown("**Foreign Key Relationships:**")
                        for fk in fks_list:
                            st.markdown(
                                f"- `{fk['column']}` → "
                                f"`{fk['ref_schema']}.{fk['ref_table']}.{fk['ref_column']}`"
                            )

                except Exception as exc:
                    st.error(f"Could not load schema for {label}: {exc}")

# ══════════════════════════════════════════════
# TAB 2 — Query Assistant
# ══════════════════════════════════════════════
with tab_query:

    if not selected_tables:
        st.warning(
            "⚠️ No tables selected. Pick at least one table from the sidebar "
            "so the AI knows your schema."
        )
        st.stop()

    # ── Schema context summary ──
    with st.expander("📄 Active Schema Context", expanded=False):
        try:
            schema_text = get_selected_schema_text(conn, selected_tables)
            st.code(schema_text, language="sql")
        except Exception as exc:
            st.error(f"Could not load schema: {exc}")
            st.stop()

    st.divider()

    # ── Chat history display ──
    for entry in st.session_state["chat_history"]:
        with st.chat_message("user"):
            st.markdown(entry["question"])
        with st.chat_message("assistant"):
            st.markdown("**Generated T-SQL**")
            st.code(entry["sql"], language="sql")
            if entry.get("error"):
                st.error(entry["error"])
            else:
                if entry.get("df") is not None:
                    st.dataframe(entry["df"], use_container_width=True)
                    st.caption(f"{entry['row_count']} row(s) returned")
                if entry.get("summary"):
                    st.markdown("**Answer:**")
                    st.info(entry["summary"])

    # ── Input ──
    question = st.chat_input(
        "Ask a question about your data…",
        key="mssql_chat_input",
    )

    if question:
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            # 1. Generate T-SQL
            with st.spinner("Generating T-SQL…"):
                try:
                    schema_text = get_selected_schema_text(conn, selected_tables)
                    sql = generate_tsql(question, schema_text, model=selected_model)
                except Exception as exc:
                    st.error(f"SQL generation failed: {exc}")
                    st.stop()

            st.markdown("**Generated T-SQL**")
            st.code(sql, language="sql")

            # 2. Validate
            is_safe, reason = validate_tsql(sql)
            if not is_safe:
                error_msg = f"Unsafe query blocked: {reason}"
                st.error(error_msg)
                st.session_state["chat_history"].append({
                    "question": question,
                    "sql": sql,
                    "error": error_msg,
                    "df": None,
                    "row_count": 0,
                    "summary": None,
                })
                st.stop()

            # 3. Execute
            with st.spinner("Running query…"):
                try:
                    columns, rows = execute_tsql(conn, sql)
                except Exception as exc:
                    error_msg = f"Query execution error: {exc}"
                    st.error(error_msg)
                    st.session_state["chat_history"].append({
                        "question": question,
                        "sql": sql,
                        "error": error_msg,
                        "df": None,
                        "row_count": 0,
                        "summary": None,
                    })
                    st.stop()

            # 4. Display results
            df = None
            if rows:
                df = pd.DataFrame(rows, columns=columns)
                st.dataframe(df, use_container_width=True)
                st.caption(f"{len(rows)} row(s) returned")
            else:
                st.warning("No records returned.")

            # 5. AI summary
            with st.spinner("Summarising answer…"):
                summary = generate_answer_summary(question, sql, columns, rows, model=selected_model)

            st.markdown("**Answer:**")
            st.info(summary)

            # 6. Persist
            save_chat(question, sql, summary)
            st.session_state["chat_history"].append({
                "question": question,
                "sql": sql,
                "error": None,
                "df": df,
                "row_count": len(rows),
                "summary": summary,
            })

    # ── Clear chat ──
    if st.session_state["chat_history"]:
        if st.button("🗑️ Clear conversation", key="clear_chat"):
            st.session_state["chat_history"] = []
            st.rerun()
