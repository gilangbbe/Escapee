"""
llama-cpp-python client — drop-in replacement for OllamaClient.

Loads a .gguf model file directly into memory (no Ollama needed).
Uses asyncio.to_thread() so the blocking inference does not freeze the server.

Usage:
  Set environment variable MODEL_PATH to the path of your .gguf file.
  The backend will automatically use this client instead of Ollama.

Example:
  export MODEL_PATH="/Users/you/models/qwen2.5-7b-instruct-q4_k_m.gguf"
  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

_llama_instance: Any = None
_llama_model_path: str = ""


def _get_llama(model_path: str, n_ctx: int = 4096, n_gpu_layers: int = -1) -> Any:
    """Load the model once and reuse the same instance for all requests.

    n_gpu_layers=-1 means use all GPU layers (Metal on Apple Silicon).
    """
    global _llama_instance, _llama_model_path
    if _llama_instance is None or _llama_model_path != model_path:
        from llama_cpp import Llama  # type: ignore
        print(f"[llama.cpp] Loading model: {model_path}")
        _llama_instance = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
        _llama_model_path = model_path
        print(f"[llama.cpp] Model loaded OK")
    return _llama_instance


class LlamaCppClient:
    """Drop-in replacement for OllamaClient using llama-cpp-python.

    The chat() method signature is identical to OllamaClient so no other
    file needs to change.
    """

    def __init__(
        self,
        model_path: str,
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,
    ) -> None:
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        # Model field kept for compatibility (used in setup_message).
        self.model = os.path.basename(model_path)

    def _run_chat(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        format_schema: dict | None,
    ) -> str:
        """Blocking inference — called inside asyncio.to_thread."""
        llm = _get_llama(self.model_path, self.n_ctx, self.n_gpu_layers)

        kwargs: dict = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
        }

        # Structured output: pass JSON schema so model is constrained to it.
        if format_schema is not None:
            kwargs["response_format"] = {
                "type": "json_object",
                "schema": format_schema,
            }

        result = llm.create_chat_completion(**kwargs)
        return result["choices"][0]["message"]["content"]

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        format_schema: dict | None = None,
        temperature: float = 0.7,
    ) -> str:
        """Async wrapper — same signature as OllamaClient.chat()."""
        return await asyncio.to_thread(
            self._run_chat, messages, temperature, format_schema
        )


def get_model_path() -> str | None:
    """Read MODEL_PATH from environment variable."""
    return os.environ.get("MODEL_PATH")


def list_gguf_models(models_dir: str | None = None) -> list[str]:
    """Scan a directory for .gguf files. Used by /api/personas model list."""
    directory = models_dir or os.environ.get("MODELS_DIR", "")
    if not directory or not os.path.isdir(directory):
        return []
    return sorted(
        f for f in os.listdir(directory) if f.endswith(".gguf")
    )
