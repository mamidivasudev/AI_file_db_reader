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