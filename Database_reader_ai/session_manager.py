import uuid

active_sessions = {}


def create_session(connection_info):
    session_id = str(uuid.uuid4())

    active_sessions[session_id] = connection_info

    return session_id


def get_session(session_id):
    return active_sessions.get(session_id)


def remove_session(session_id):
    active_sessions.pop(session_id, None)


file_sessions = {}


def get_file_session_history(session_id: str) -> list:
    if not session_id:
        return []
    return file_sessions.get(session_id, [])


def add_file_session_history(session_id: str, question: str, answer: str, max_history: int = 5):
    if not session_id:
        return
    if session_id not in file_sessions:
        file_sessions[session_id] = []
    file_sessions[session_id].append({"question": question, "answer": answer})
    if len(file_sessions[session_id]) > max_history:
        file_sessions[session_id] = file_sessions[session_id][-max_history:]