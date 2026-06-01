import ollama as _ollama
from ollama import chat

DEFAULT_MODEL = "qwen2.5-coder:7b"


def list_ollama_models():
    """Return list of locally available Ollama model names."""
    try:
        result = _ollama.list()
        # result.models is a list of Model objects with a .model attribute
        return [m.model for m in result.models]
    except Exception:
        return []


def ask_ollama(prompt, model=DEFAULT_MODEL):
    response = chat(
        model=model,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        return response["message"]["content"]
    except Exception:
        return response.message.content
