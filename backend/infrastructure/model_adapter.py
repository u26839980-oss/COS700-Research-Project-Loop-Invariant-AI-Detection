from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from backend.infrastructure.local_model_adapter import OllamaModelAdapter
from backend.infrastructure.candidate_parser import extract_scalar_candidates


def gemini_api_key() -> str:
    if os.getenv("LOOPS_LOCAL_ONLY") == "1":
        return ""
    secret_file = os.getenv("GEMINI_API_KEY_FILE", "")
    if secret_file:
        return Path(secret_file).read_text(encoding="utf-8").strip()
    return os.getenv("GEMINI_API_KEY", "").strip()


class ModelAdapter:
    """Route provider-qualified names while preserving legacy Ollama names."""

    extract_candidates = staticmethod(extract_scalar_candidates)

    def __init__(self) -> None:
        self.ollama = OllamaModelAdapter()
        self.last_generation_metadata: dict[str, object] = {}
        self.gemini_models = [
            name.strip() for name in os.getenv("GEMINI_MODELS", "gemini-3.8-flash").split(",")
            if name.strip()
        ]

    def list_models(self) -> list[str]:
        models = list(self.ollama.list_models())
        if gemini_api_key():
            models.extend(f"gemini/{name}" for name in self.gemini_models)
        return models

    @staticmethod
    def provider(model_name: str) -> str:
        return "gemini" if model_name.startswith("gemini/") else "ollama"

    def generate(self, model_name: str, prompt: str, timeout_seconds: int = 90,
                 temperature: float = 0, seed: int | None = None,
                 max_output_tokens: int | None = None, context_length: int = 4096,
                 repeat_penalty: float | None = None) -> str:
        if os.getenv("LOOPS_LOCAL_ONLY") == "1" and self.provider(model_name) != "ollama":
            return "MODEL_ERROR: Hosted providers are disabled in local-only deployment."
        token_limit = max_output_tokens or (2048 if self.provider(model_name) == "gemini" else 140)
        self.last_generation_metadata = {
            "provider": self.provider(model_name),
            "requested_model": model_name,
            "temperature": temperature,
            "max_output_tokens": token_limit,
            "timeout_seconds": timeout_seconds,
            "seed": seed,
            "context_length": context_length if self.provider(model_name) == "ollama" else "provider-managed",
            "repeat_penalty": repeat_penalty if self.provider(model_name) == "ollama" else "not applicable",
        }
        if self.provider(model_name) == "ollama":
            if seed is None and max_output_tokens is None and context_length == 4096 and repeat_penalty is None:
                output = self.ollama.generate(model_name, prompt, timeout_seconds, temperature)
            else:
                output = self.ollama.generate(model_name, prompt, timeout_seconds, temperature,
                                              seed, token_limit, context_length, repeat_penalty)
            self.last_generation_metadata.update(getattr(self.ollama, "last_response_metadata", {}))
            return output
        key = gemini_api_key()
        if not key:
            return "MODEL_ERROR: Gemini requires a server-side GEMINI_API_KEY."
        model = model_name.removeprefix("gemini/")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
            return "MODEL_ERROR: Invalid Gemini model ID."
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": token_limit},
        }
        if seed is not None:
            payload["generationConfig"]["seed"] = seed
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Do not return request headers, credentials, or provider response bodies.
            return f"MODEL_ERROR: Gemini HTTP {exc.code}; check model access, quota, and credentials."
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return "MODEL_ERROR: Gemini request failed or timed out."
        self.last_generation_metadata.update({
            "model_version": body.get("modelVersion", ""),
            "usage": body.get("usageMetadata", {}),
        })
        candidates = body.get("candidates", [])
        if not candidates:
            return "MODEL_ERROR: Gemini returned no candidate (possibly blocked)."
        candidate = candidates[0]
        self.last_generation_metadata["finish_reason"] = candidate.get("finishReason", "")
        if candidate.get("finishReason") not in (None, "STOP"):
            return f"MODEL_ERROR: Gemini generation did not complete: {candidate.get('finishReason')}."
        parts = candidate.get("content", {}).get("parts", [])
        output = "\n".join(part.get("text", "") for part in parts if not part.get("thought"))
        return output.strip() or "MODEL_ERROR: Gemini returned no text."
