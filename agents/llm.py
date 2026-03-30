"""Local LLM interface via Ollama (Qwen3 32B)."""

import logging
import os

logger = logging.getLogger(__name__)

MODEL = "qwen3:32b"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")


def _get_client():
    import ollama
    return ollama.Client(host=OLLAMA_HOST)


def ask(prompt: str, system: str = "", temperature: float = 0.3, max_tokens: int = 2048) -> str:
    """Send a prompt to the local Qwen3 model and return the response text."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        client = _get_client()
        response = client.chat(
            model=MODEL,
            messages=messages,
            options={"temperature": temperature, "num_predict": max_tokens},
            think=False,  # Disable thinking mode — get direct answers
        )

        content = response.message.content or ""
        return content.strip()

    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return f"[LLM unavailable: {e}]"


def is_available() -> bool:
    """Check if Ollama is running and the model is loaded."""
    try:
        client = _get_client()
        models = client.list()
        names = [m.model for m in models.models] if hasattr(models, 'models') else []
        return any(MODEL.split(":")[0] in n for n in names)
    except Exception:
        return False
