import ollama as _ollama
from ollama import chat, AsyncClient

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
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0}
    )
    try:
        return response["message"]["content"]
    except Exception:
        return response.message.content

async def ask_ollama_stream(prompt, model=DEFAULT_MODEL):
    client = AsyncClient()
    response = await client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0},
        stream=True
    )
    async for chunk in response:
        try:
            yield chunk["message"]["content"]
        except Exception:
            yield chunk.message.content
