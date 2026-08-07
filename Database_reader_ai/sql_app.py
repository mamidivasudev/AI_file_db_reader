import pandas as pd
import streamlit as st

from sql_connector import connect_mssql, get_available_drivers
from ollama_client import list_ollama_models, ask_ollama
from sql_schema_reader import (
    get_all_tables,
    get_table_columns,
    get_primary_keys,
    get_foreign_keys,
    get_selected_schema_text,
)
from sql_generator import generate_tsql, generate_answer_summary
from sql_executor import validate_tsql, execute_tsql
from history_manager import init_db, save_chat, get_business_rules, save_business_rules, save_user_suggestion, get_user_suggestions
from file_reader import read_project
from search_engine import search_files

# Import unified adapters
from db_adapters import (
    DATABASES,
    connect_db,
    get_db_tables,
    get_db_table_metadata,
    get_db_schema_text,
    generate_db_query,
    validate_db_query,
    execute_db_query,
    generate_db_answer_summary,
    generate_suggested_questions,
)

# ─────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────
init_db()

st.set_page_config(
    page_title="Database AI Assistant",
    layout="wide",
    page_icon="🗄️",
)

# Inject custom CSS to reduce top padding in sidebar and main content
st.markdown(
    """
    <style>
        [data-testid="stSidebarHeader"] {
            display: none !important;
        }
        [data-testid="stSidebar"] {
            padding-top: 0rem !important;
        }
        [data-testid="stSidebarUserContent"] {
            padding-top: 1.5rem !important;
        }
        .stMainBlockContainer {
            padding-top: 3rem !important;
        }
        
        /* Hide scrollbars in sidebar */
        [data-testid="stSidebar"]::-webkit-scrollbar,
        [data-testid="stSidebar"] > div::-webkit-scrollbar,
        [data-testid="stSidebarUserContent"]::-webkit-scrollbar {
            display: none !important;
        }
        [data-testid="stSidebar"],
        [data-testid="stSidebar"] > div,
        [data-testid="stSidebarUserContent"] {
            -ms-overflow-style: none !important;
            scrollbar-width: none !important;
        }

        /* Compact vertical layout block in sidebar */
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            gap: 0.4rem !important;
        }
        
        /* Compact markdown texts and labels in sidebar */
        [data-testid="stSidebar"] div[data-testid="stMarkdownContainer"] p {
            margin-bottom: 0.2rem !important;
            margin-top: 0rem !important;
            padding-top: 0rem !important;
            line-height: 1.2 !important;
            font-size: 0.85rem !important;
            overflow: visible !important;
        }
        [data-testid="stSidebar"] label {
            margin-bottom: 0.2rem !important;
            margin-top: 0rem !important;
            padding-top: 0rem !important;
            line-height: 1.2 !important;
            font-size: 0.85rem !important;
            overflow: visible !important;
        }
        
        /* Compact selectboxes, text inputs, etc. */
        [data-testid="stSidebar"] div[data-baseweb="select"] {
            min-height: 44px !important;
        }
        [data-testid="stSidebar"] div[data-baseweb="base-input"] {
            min-height: 44px !important;
        }
        [data-testid="stSidebar"] input {
            padding-top: 0.25rem !important;
            padding-bottom: 0.25rem !important;
            font-size: 0.85rem !important;
        }
        [data-testid="stSidebar"] div[role="listbox"] {
            font-size: 0.85rem !important;
        }
        
        /* Hide the sidebar collapse (back) button */
        [data-testid="stSidebarCollapseButton"],
        [data-testid="collapsedControl"] {
            display: none !important;
        }
        
        /* Hide Ctrl+Enter instructions on text areas */
        div[data-testid="InputInstructions"] {
            display: none !important;
        }

    </style>
    """,
    unsafe_allow_html=True
)


# ─────────────────────────────────────────────
# Session state defaults
# ─────────────────────────────────────────────
for key, default in {
    "db_type": "MS SQL",
    "mssql_conn": None,
    "all_tables": [],
    "selected_tables": [],
    "chat_history": [],
    "table_multiselect": [],
    "project_files": [],
    "project_path": "",
    "project_answer": "",
    "suggested_questions": [],
    "last_schema_for_questions": "",
    "pending_question": None,
    "db_identifier": "",
    "business_rules": "",
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ─────────────────────────────────────────────
# Sidebar — Configuration
# ─────────────────────────────────────────────
with st.sidebar:
    # Mode Toggle
    is_file_reader = st.toggle("📁 Switch to File Reader / Database Mode", value=False)
    mode = "File Reader AI Assistant" if is_file_reader else "Database AI Assistant"

    # Database selection above model (only shown if not in File Reader mode)
    if mode == "Database AI Assistant":
        db_type = st.selectbox(
            "🗄️ Database Type",
            options=DATABASES,
            index=DATABASES.index(st.session_state["db_type"]) if st.session_state["db_type"] in DATABASES else 0,
        )

        # Sync with session state and disconnect on change
        if st.session_state["db_type"] != db_type:
            if st.session_state["mssql_conn"] is not None:
                try:
                    st.session_state["mssql_conn"].close()
                except Exception:
                    pass
            st.session_state["mssql_conn"] = None
            st.session_state["all_tables"] = []
            st.session_state["selected_tables"] = []
            st.session_state.pop("table_multiselect", None)
            st.session_state["chat_history"] = []
            st.session_state["db_type"] = db_type
            st.rerun()
    else:
        db_type = st.session_state["db_type"]

    # Shared model selection for both modes
    available_models = list_ollama_models()

    if available_models:
        selected_model = st.selectbox(
            "🧠 Model",
            options=available_models,
        )
    else:
        st.warning("No Ollama models found. Run `ollama pull <model>` first.")
        selected_model = st.text_input("🧠 Model", value="qwen2.5-coder:7b")

    if mode == "File Reader AI Assistant":
        project_path = st.text_input("📁 Project Folder Path", placeholder="C:\\MyProject")

        if st.button("Load Project", width="stretch"):
            try:
                files = read_project(project_path)
                st.session_state["project_files"] = files
                st.session_state["project_path"] = project_path
                st.session_state["project_answer"] = ""
                st.success(f"{len(files)} files loaded")
            except Exception as e:
                st.error(str(e))

    if mode == "Database AI Assistant":
        conn_params = {}
        connect_disabled = False

        if db_type == "MS SQL":
            available_drivers = get_available_drivers()
            if available_drivers:
                driver = st.selectbox(
                    "🔌 ODBC Driver",
                    options=available_drivers,
                    index=len(available_drivers) - 1,
                )
            else:
                st.error(
                    "No SQL Server ODBC driver found.\n"
                    "Install **ODBC Driver 17** or **18 for SQL Server**."
                )
                driver = None
                connect_disabled = True

            c1, c2 = st.columns(2)
            server = c1.text_input("Server", placeholder="Host\\Instance", label_visibility="collapsed")
            database = c2.text_input("Database", placeholder="Database", label_visibility="collapsed")

            auth_mode = st.selectbox(
                "Auth",
                ["Windows Authentication", "SQL Server Authentication"],
                label_visibility="collapsed"
            )

            username = password = None
            if auth_mode == "SQL Server Authentication":
                c3, c4 = st.columns(2)
                username = c3.text_input("Username", placeholder="Username", label_visibility="collapsed")
                password = c4.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")

            conn_params = {
                "server": server,
                "database": database,
                "auth_mode": auth_mode,
                "username": username,
                "password": password,
                "driver": driver
            }

        elif db_type == "MySQL":
            c1, c2 = st.columns(2)
            host = c1.text_input("Host", value="localhost", placeholder="localhost")
            port = c2.number_input("Port", value=3306)

            c3, c4 = st.columns(2)
            database = c3.text_input("Database", placeholder="Database")
            username = c4.text_input("Username", value="root", placeholder="root")

            password = st.text_input("Password", type="password", placeholder="Password")

            conn_params = {
                "host": host,
                "port": port,
                "database": database,
                "username": username,
                "password": password
            }

        elif db_type == "PostgreSQL":
            c1, c2 = st.columns(2)
            host = c1.text_input("Host", value="localhost", placeholder="localhost")
            port = c2.number_input("Port", value=5432)

            c3, c4 = st.columns(2)
            database = c3.text_input("Database", placeholder="Database")
            username = c4.text_input("Username", value="postgres", placeholder="postgres")

            password = st.text_input("Password", type="password", placeholder="Password")

            conn_params = {
                "host": host,
                "port": port,
                "database": database,
                "username": username,
                "password": password
            }

        elif db_type == "Oracle Database":
            c1, c2 = st.columns(2)
            host = c1.text_input("Host", value="localhost", placeholder="localhost")
            port = c2.number_input("Port", value=1521)

            c3, c4 = st.columns(2)
            service_name = c3.text_input("Service Name / SID", value="ORCL", placeholder="ORCL")
            username = c4.text_input("Username", placeholder="Username")

            password = st.text_input("Password", type="password", placeholder="Password")

            conn_params = {
                "host": host,
                "port": port,
                "service_name": service_name,
                "username": username,
                "password": password
            }

        elif db_type == "SQLite":
            db_path = st.text_input("SQLite Database File Path", value="local.db", placeholder="path/to/database.db")
            conn_params = {
                "db_path": db_path
            }

        elif db_type == "MongoDB":
            uri = st.text_input("MongoDB Connection URI", value="mongodb://localhost:27017/")
            database = st.text_input("Database Name", value="test")
            conn_params = {
                "uri": uri,
                "database": database
            }

        elif db_type == "Redis":
            c1, c2 = st.columns(2)
            host = c1.text_input("Host", value="localhost", placeholder="localhost")
            port = c2.number_input("Port", value=6379)

            c3, c4 = st.columns(2)
            password = c3.text_input("Password", type="password", placeholder="Password")
            db_index = c4.number_input("DB Index", value=0, min_value=0)

            conn_params = {
                "host": host,
                "port": port,
                "password": password,
                "db_index": db_index
            }

        connect_clicked = st.button(
            "Connect",
            width="stretch",
            type="primary",
            disabled=connect_disabled,
        )

        if connect_clicked:
            if db_type == "MS SQL" and (not conn_params.get("server") or not conn_params.get("database")):
                st.error("Please fill in Server and Database.")
            elif db_type in ["MySQL", "PostgreSQL", "Oracle Database"] and (not conn_params.get("database") and db_type != "Oracle Database" or not conn_params.get("host")):
                st.error("Please fill in Host and Database/Service Name.")
            elif db_type == "SQLite" and not conn_params.get("db_path"):
                st.error("Please fill in Database File Path.")
            elif db_type == "MongoDB" and (not conn_params.get("uri") or not conn_params.get("database")):
                st.error("Please fill in URI and Database Name.")
            else:
                with st.spinner("Connecting…"):
                    try:
                        conn = connect_db(db_type, conn_params)
                        st.session_state["mssql_conn"] = conn
                        st.session_state["all_tables"] = get_db_tables(conn, db_type)
                        st.session_state["selected_tables"] = []
                        st.session_state.pop("table_multiselect", None)
                        st.session_state["chat_history"] = []
                        
                        # Generate DB Identifier
                        db_ident = f"{db_type}_{conn_params.get('database') or conn_params.get('db_path') or conn_params.get('host') or 'default'}"
                        st.session_state["db_identifier"] = db_ident
                        st.session_state["business_rules"] = get_business_rules(db_ident)
                        if "business_rules_widget" in st.session_state:
                            del st.session_state["business_rules_widget"]
                    except Exception as exc:
                        st.error(f"Connection failed:\n{exc}")

        if st.session_state["mssql_conn"] is not None:
            st.success(f"✅ Connected to **{db_type}**!")
            label_name = "Collections" if db_type == "MongoDB" else ("Key Patterns" if db_type == "Redis" else "Tables")

            all_tables = st.session_state["all_tables"]
            if db_type in ["MS SQL", "PostgreSQL", "Oracle Database"]:
                table_labels = [f"{s}.{t}" for s, t in all_tables]
            else:
                table_labels = [t for s, t in all_tables]

            if not table_labels:
                st.warning(f"No {label_name.lower()} found.")
            else:
                st.multiselect(
                    label_name,
                    options=table_labels,
                    key="table_multiselect",
                    label_visibility="collapsed",
                    placeholder=f"📋 Select {label_name}...",
                )

                if db_type in ["MS SQL", "PostgreSQL", "Oracle Database"]:
                    st.session_state["selected_tables"] = [
                        (lbl.split(".", 1)[0], lbl.split(".", 1)[1])
                        for lbl in st.session_state["table_multiselect"]
                    ]
                else:
                    st.session_state["selected_tables"] = [
                        ("Default", lbl)
                        for lbl in st.session_state["table_multiselect"]
                    ]

            if st.button("🔌 Disconnect", width="stretch"):
                try:
                    st.session_state["mssql_conn"].close()
                except Exception:
                    pass

                st.session_state["mssql_conn"] = None
                st.session_state["all_tables"] = []
                st.session_state["selected_tables"] = []
                st.session_state.pop("table_multiselect", None)
                st.session_state["chat_history"] = []
                st.session_state["db_identifier"] = ""
                st.session_state["business_rules"] = ""
                st.rerun()

    st.divider()
    if st.button("🔄 Reset App", width="stretch", help="Reset all inputs and settings"):
        if "mssql_conn" in st.session_state and st.session_state["mssql_conn"] is not None:
            try:
                st.session_state["mssql_conn"].close()
            except Exception:
                pass
        st.session_state.clear()
        st.rerun()


# ─────────────────────────────────────────────
# Main content — File Reader Mode
# ─────────────────────────────────────────────
if mode == "File Reader AI Assistant":
    st.title("📁 File Reader AI Assistant")

    if not st.session_state["project_files"]:
        st.info("Load a project folder from the sidebar.")
        st.stop()

    project_question = st.chat_input("Ask about the project...")

    if project_question:
        if not project_question.strip():
            st.warning("Please enter a question.")
            st.stop()

        matched_files = search_files(
            project_question,
            st.session_state["project_files"]
        )

        prompt = ""

        for file in matched_files:
            prompt += f"\n\nFILE: {file['filename']}\n"
            prompt += file["content"][:5000]

        prompt += f"\n\nQuestion:\n{project_question}"

        with st.spinner("Analyzing project..."):
            answer = ask_ollama(
                prompt,
                model=selected_model
            )

        st.session_state["project_answer"] = answer

        st.subheader("Matched Files")
        for file in matched_files:
            st.code(file["path"])

    if st.session_state["project_answer"]:
        st.subheader("Answer")
        st.write(st.session_state["project_answer"])

    st.stop()


# ─────────────────────────────────────────────
# Main content — Database Mode
# ─────────────────────────────────────────────
if mode == "Database AI Assistant" and st.session_state["mssql_conn"] is None:
    st.title(f"🗄️ {db_type} AI Assistant")
    desc_query_lang = "T-SQL" if db_type == "MS SQL" else ("NoSQL Queries" if db_type in ["MongoDB", "Redis"] else "SQL")
    st.markdown(
        f"""
        Connect to your **{db_type}** database using the sidebar, then:

        1. **Browse** your schema structure (tables, columns, collections, or keys)
        2. **Select** the items relevant to your question
        3. **Ask** a natural-language question — the AI generates {desc_query_lang}, runs it, and explains the answer
        """
    )
    st.info("👈 Fill in the connection details in the sidebar to get started.")
    st.stop()

conn = st.session_state["mssql_conn"]
selected_tables = st.session_state["selected_tables"]

tab_schema, tab_query = st.tabs(["📐 Schema Browser", "💬 Query Assistant"])

with tab_schema:
    all_tables = st.session_state["all_tables"]

    if not all_tables:
        st.warning("No items found.")
    else:
        browse_target = selected_tables if selected_tables else all_tables

        label_name = "collection(s)" if db_type == "MongoDB" else ("key pattern(s)" if db_type == "Redis" else "table(s)")
        if selected_tables:
            st.info(
                f"Showing **{len(selected_tables)}** selected {label_name}. "
                "Deselect items in the sidebar to browse all."
            )
        else:
            st.info(
                f"Showing all **{len(all_tables)}** {label_name}. "
                "Select items in the sidebar to filter."
            )

        search = st.text_input(
            f"🔍 Filter {label_name}",
            placeholder=f"Type to filter by name…",
        )

        for schema_name, table_name in browse_target:
            label = f"{schema_name}.{table_name}" if db_type in ["MS SQL", "PostgreSQL", "Oracle Database"] else table_name
            if search and search.lower() not in label.lower():
                continue

            with st.expander(f"**{label}**", expanded=False):
                try:
                    metadata = get_db_table_metadata(conn, db_type, schema_name, table_name)
                    columns = metadata["columns"]
                    pks = set(metadata["pks"])
                    fks_list = metadata["fks"]
                    fk_map = {fk["column"]: fk for fk in fks_list}

                    rows_data = []
                    for col in columns:
                        type_str = col["type"].upper()
                        if col.get("max_length"):
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
                            "Column/Field": col["name"],
                            "Type/Value": type_str,
                            "Nullable": col.get("nullable", "YES"),
                            "Default": col.get("default") or "",
                            "Keys/Details": "  ".join(badges),
                        })

                    df_schema = pd.DataFrame(rows_data)
                    st.dataframe(
                        df_schema,
                        width="stretch",
                        hide_index=True,
                    )

                    if fks_list:
                        st.markdown("**Foreign Key Relationships:**")
                        for fk in fks_list:
                            st.markdown(
                                f"- `{fk['column']}` → "
                                f"`{fk['ref_schema']}.{fk['ref_table']}.{fk['ref_column']}`"
                            )

                except Exception as exc:
                    st.error(f"Could not load structure for {label}: {exc}")

with tab_query:
    col_empty, col_clear_top = st.columns([8, 2])
    with col_clear_top:
        if st.session_state.get("chat_history"):
            if st.button("🗑️ Clear conversation", key="clear_chat_top"):
                st.session_state["chat_history"] = []
                st.rerun()

    label_name = "collections" if db_type == "MongoDB" else ("key patterns" if db_type == "Redis" else "tables")
    if not selected_tables:
        st.warning(
            f"⚠️ No {label_name} selected. Pick at least one item from the sidebar "
            "so the AI knows your schema."
        )
        st.stop()

    with st.expander("📄 Active Schema Context", expanded=False):
        with st.container(height=350):
            try:
                schema_text = get_db_schema_text(conn, db_type, selected_tables)
                code_lang = "json" if db_type in ["MongoDB", "Redis"] else "sql"
                st.code(schema_text, language=code_lang)
            except Exception as exc:
                st.error(f"Could not load schema: {exc}")
                st.stop()

    with st.expander("🧠 Custom Business Rules", expanded=False):
        
        def on_rules_change():
            st.session_state["business_rules"] = st.session_state["business_rules_widget"]
            save_business_rules(st.session_state.get("db_identifier", ""), st.session_state["business_rules"])

        def on_save_next():
            import re
            raw_text = st.session_state.get("business_rules_widget", "")
            lines = raw_text.strip().split('\n') if raw_text.strip() else []
            cleaned_lines = []
            for line in lines:
                cleaned_line = re.sub(r'^\d+\.\s*', '', line.strip())
                if cleaned_line:
                    cleaned_lines.append(cleaned_line)
            
            numbered_text = ""
            for i, c_line in enumerate(cleaned_lines):
                numbered_text += f"{i+1}. {c_line}\n"
            
            next_num = len(cleaned_lines) + 1
            numbered_text += f"{next_num}. "
            
            st.session_state["business_rules"] = numbered_text
            st.session_state["business_rules_widget"] = numbered_text
            save_business_rules(st.session_state.get("db_identifier", ""), numbered_text)

        if "business_rules_widget" not in st.session_state:
            st.session_state["business_rules_widget"] = st.session_state.get("business_rules", "")

        st.text_area(
            "Add specific rules or formulas for the AI (e.g., 'Total length means EndCh - StartCh')",
            height=350,
            key="business_rules_widget",
            on_change=on_rules_change
        )
        
        st.button("💾 Save & Next", help="Saves rule and numbers the next line", on_click=on_save_next)

    with st.expander("💡 Suggested Questions", expanded=False):
        try:
            current_schema_text = get_db_schema_text(conn, db_type, selected_tables)
        except Exception:
            current_schema_text = ""
            
        if st.session_state["last_schema_for_questions"] != current_schema_text:
            st.session_state["suggested_questions"] = []
            st.session_state["last_schema_for_questions"] = current_schema_text
        
        with st.container(height=350):
            # Render User Saved Suggestions
            saved_suggs = get_user_suggestions(db_type, selected_tables)
            if saved_suggs:
                st.markdown("#### 📌 Your Saved Suggestions")
                for i, sq in enumerate(saved_suggs):
                    if st.button(f"🔍 {sq}", key=f"saved_btn_{i}"):
                        st.session_state["pending_question"] = sq
                st.divider()

            st.markdown("#### ✨ AI Generated Suggestions")
            if not st.session_state["suggested_questions"]:
                if st.button("✨ Generate New Suggestions", help="Let AI suggest what you can ask based on your selected schema"):
                    with st.spinner("Analyzing schema to suggest questions..."):
                        try:
                            suggs = generate_suggested_questions(current_schema_text, model=selected_model)
                            st.session_state["suggested_questions"] = suggs
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Failed to generate suggestions: {exc}")
            
            if st.session_state["suggested_questions"]:
                for i, sq in enumerate(st.session_state["suggested_questions"]):
                    if st.button(f"🔍 {sq}", key=f"ai_sq_btn_{i}"):
                        st.session_state["pending_question"] = sq

    st.divider()

    query_lang_name = "Query"
    query_code_lang = "sql"
    if db_type == "MS SQL":
        query_lang_name = "T-SQL"
        query_code_lang = "sql"
    elif db_type in ["MongoDB", "Redis"]:
        query_lang_name = "JSON Command"
        query_code_lang = "json"

    for i, entry in enumerate(st.session_state["chat_history"]):
        with st.chat_message("user"):
            st.markdown(entry["question"])
        with st.chat_message("assistant"):
            st.markdown(f"**Generated {query_lang_name}**")
            st.code(entry["sql"], language=query_code_lang)
            if entry.get("error"):
                st.error(entry["error"])
            else:
                if entry.get("df") is not None:
                    st.dataframe(entry["df"], width="stretch")
                    st.caption(f"{entry['row_count']} row(s) returned")
                if entry.get("summary"):
                    st.markdown("**Answer:**")
                    st.info(entry["summary"])
                    
                cols = st.columns([1, 4])
                with cols[0]:
                    if st.button("💾 Save as Suggestion", key=f"save_sug_{i}"):
                        save_user_suggestion(db_type, selected_tables, entry["question"])
                        st.toast("✅ Question saved as a custom suggestion!")

    question_clicked = st.session_state.get("pending_question")
    if question_clicked:
        st.session_state["pending_question"] = None

    action_cols = st.columns([2, 2, 5])
    with action_cols[0]:
        with st.popover("✍️ Write Query"):
            raw_sql_input = st.text_area("Write your raw SQL query here:", height=150, key="raw_sql_text_main")
            if st.button("▶️ Run Query", use_container_width=True):
                st.session_state["raw_query_pending"] = raw_sql_input
                st.rerun()
    
    with action_cols[1]:
        if st.session_state["chat_history"]:
            if st.button("🗑️ Clear conversation", key="clear_chat"):
                st.session_state["chat_history"] = []
                st.rerun()

    question_input = st.chat_input(
        "Ask a question about your data…",
        key="db_chat_input",
    )
    
    question = question_input or question_clicked
    raw_query = st.session_state.get("raw_query_pending")
    if raw_query:
        st.session_state["raw_query_pending"] = None

    if question or raw_query:
        display_text = raw_query if raw_query else question
        with st.chat_message("user"):
            st.markdown(f"```sql\n{display_text}\n```" if raw_query else display_text)

        with st.chat_message("assistant"):
            if raw_query:
                sql = raw_query
            else:
                with st.spinner(f"Generating {query_lang_name}…"):
                    try:
                        schema_text = get_db_schema_text(conn, db_type, selected_tables)
                        sql = generate_db_query(
                            db_type, 
                            question, 
                            schema_text, 
                            business_rules=st.session_state.get("business_rules", ""),
                            model=selected_model
                        )
                    except Exception as exc:
                        st.error(f"Query generation failed: {exc}")
                        st.stop()

            st.markdown(f"**Executing {query_lang_name}**" if raw_query else f"**Generated {query_lang_name}**")
            st.code(sql, language=query_code_lang)

            is_safe, reason = validate_db_query(db_type, sql)
            if not is_safe:
                error_msg = f"Unsafe query blocked: {reason}"
                st.error(error_msg)
                
                question_to_save = raw_query if raw_query else question
                st.session_state["chat_history"].append({
                    "question": f"```sql\n{raw_query}\n```" if raw_query else question,
                    "sql": sql,
                    "error": error_msg,
                    "df": None,
                    "row_count": 0,
                    "summary": None,
                })
                st.stop()

            with st.spinner("Running query…"):
                try:
                    columns, rows = execute_db_query(conn, db_type, sql)
                except Exception as exc:
                    exc_str = str(exc)
                    if any(err in exc_str for err in ["Invalid column name", "Invalid object name", "Incorrect syntax", "could not be bound"]):
                        error_msg = f"⚠️ **Missing Tables or Invalid Query:** Please make sure you have selected **all required tables** from the left sidebar before asking your question.\n\n*(Original Error: {exc_str})*"
                    else:
                        error_msg = f"Query execution error: {exc_str}"
                    st.error(error_msg)
                    question_to_save = raw_query if raw_query else question
                    st.session_state["chat_history"].append({
                        "question": f"```sql\n{raw_query}\n```" if raw_query else question,
                        "sql": sql,
                        "error": error_msg,
                        "df": None,
                        "row_count": 0,
                        "summary": None,
                    })
                    st.stop()

            df = None
            if rows:
                df = pd.DataFrame(rows, columns=columns)
                st.dataframe(df, width="stretch")
                st.caption(f"{len(rows)} row(s) returned")
            else:
                st.warning("No records returned.")

            with st.spinner("Summarising answer…"):
                try:
                    summary = generate_db_answer_summary(
                        db_type,
                        question,
                        sql,
                        columns,
                        rows,
                        model=selected_model
                    )
                except Exception as exc:
                    summary = f"Summary generation failed: {exc}"

            st.markdown("**Answer:**")
            st.info(summary)

            question_to_save = raw_query if raw_query else question
            save_chat(question_to_save, sql, summary)
            st.session_state["chat_history"].append({
                "question": f"```sql\n{raw_query}\n```" if raw_query else question,
                "sql": sql,
                "error": None,
                "df": df,
                "row_count": len(rows),
                "summary": summary,
            })
            st.rerun()
