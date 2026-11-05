from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from backend.infrastructure.candidate_parser import extract_scalar_candidates


class OllamaModelAdapter:
    def __init__(self) -> None:
        self.executable = self._resolve_executable()
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

    def list_models(self) -> list[str]:
        api_models = self._list_models_api()
        if api_models:
            return self._preferred_order(api_models)

        try:
            completed = subprocess.run(
                [self.executable, "list"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return []

        if completed.returncode != 0:
            return []

        lines = completed.stdout.splitlines()
        models: list[str] = []
        for line in lines[1:]:
            parts = line.split()
            if parts:
                models.append(parts[0])
        return self._preferred_order(models)

    def generate(
        self,
        model_name: str,
        prompt: str,
        timeout_seconds: int = 90,
        temperature: float = 0,
        seed: int | None = None,
        max_output_tokens: int = 140,
        context_length: int = 4096,
        repeat_penalty: float | None = None,
    ) -> str:
        self.last_response_metadata = {}
        api_output = self._generate_api(model_name, prompt, timeout_seconds, temperature,
                                        seed, max_output_tokens, context_length, repeat_penalty)
        if api_output is not None:
            return api_output
        detail = self.last_response_metadata.get("provider_error", "Ollama request failed.")
        return "MODEL_ERROR: " + detail

    extract_candidates = staticmethod(extract_scalar_candidates)

    def _preferred_order(self, models: list[str]) -> list[str]:
        preferred = ["qwen2.5-coder:1.5b", "deepseek-coder:1.3b", "codellama:7b-code"]
        ordered = [model for model in preferred if model in models]
        ordered.extend(model for model in models if model not in ordered)
        return ordered

    def _resolve_executable(self) -> str:
        from_path = shutil.which("ollama")
        if from_path:
            return from_path

        candidates = [
            Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
            Path("C:/Program Files/Ollama/ollama.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

        return "ollama"

    def _list_models_api(self) -> list[str]:
        try:
            request = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return []

        models = payload.get("models", [])
        return [model["name"] for model in models if "name" in model]

    def _generate_api(
        self,
        model_name: str,
        prompt: str,
        timeout_seconds: int,
        temperature: float,
        seed: int | None = None,
        max_output_tokens: int = 140,
        context_length: int = 4096,
        repeat_penalty: float | None = None,
    ) -> str | None:
        options = {"temperature": temperature, "num_predict": max_output_tokens, "num_ctx": context_length}
        if seed is not None:
            options["seed"] = seed
        if repeat_penalty is not None:
            options["repeat_penalty"] = repeat_penalty
        if "CANDIDATES" in prompt and "END" in prompt:
            options["stop"] = ["\nEND\n", "\r\nEND\r\n"]
        framing = {"stop_sequences": options.get("stop", [])}
        match model_name.split(":", 1)[0].split("/")[-1]:
            case "gemma4":
                endpoint = "chat"
                content = {"messages": [{"role": "user", "content": prompt}], "think": False}
            case _:
                endpoint = "generate"
                content = {"prompt": prompt}
        payload = json.dumps({"model": model_name, "stream": False, "options": options, **content}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/{endpoint}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = str(json.loads(exc.read().decode("utf-8")).get("error", ""))[:500]
            except (ValueError, UnicodeError):
                detail = "Ollama returned an HTTP error."
            lowered = detail.lower()
            kind = "generation_repetition" if "token repeat limit" in lowered else (
                "resource_exhausted" if "out of memory" in lowered else "http_error")
            self.last_response_metadata = {**framing, "http_status": exc.code,
                                           "provider_error": detail, "error_kind": kind}
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            self.last_response_metadata = {**framing, "error_kind": "transport_or_decoding",
                "provider_error": "Ollama connection, timeout, or response decoding failure."}
            return None
        self.last_response_metadata = {
            key: body.get(key) for key in (
                "model", "done_reason", "prompt_eval_count", "eval_count", "load_duration",
                "prompt_eval_duration", "eval_duration",
            )
        }
        self.last_response_metadata.update(**framing, endpoint=endpoint,
            thinking_enabled=False if endpoint == "chat" else "model_default")
        message = body.get("message", {}) if endpoint == "chat" else {}
        output = message.get("content", "") if endpoint == "chat" else body.get("response", "")
        if not isinstance(output, str) or not output.strip():
            self.last_response_metadata["provider_error"] = "Ollama returned no final answer text."
            return None
        return output.strip()
