from __future__ import annotations

import json

import httpx

from db_insight.errors import DbInsightError


class ModelClient:
    model: str

    def generate(self, prompt: str, system: str | None = None) -> str:
        raise NotImplementedError

    def health(self) -> dict:
        raise NotImplementedError


class OllamaClient(ModelClient):
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url
        self.model = model

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        if system:
            payload["system"] = system

        try:
            response = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=120)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise DbInsightError(
                f"Could not connect to Ollama at {self.base_url}. Start Ollama and pull "
                f"the Gemma model with: ollama pull {self.model}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            message = exc.response.text
            if "not found" in message.lower() or "pull" in message.lower():
                raise DbInsightError(
                    f"Ollama model '{self.model}' is not available. Pull Gemma with: "
                    f"ollama pull {self.model}"
                ) from exc
            raise DbInsightError(f"Ollama request failed: {message}") from exc
        except httpx.TimeoutException as exc:
            raise DbInsightError("Ollama took too long to respond.") from exc
        data = response.json()
        return str(data.get("response", "")).strip()

    def health(self) -> dict:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise DbInsightError(
                f"Could not connect to Ollama at {self.base_url}. Start Ollama first."
            ) from exc
        except httpx.HTTPError as exc:
            raise DbInsightError(f"Ollama health check failed: {exc}") from exc

        models = [item.get("name", "") for item in response.json().get("models", [])]
        return {
            "url": self.base_url,
            "model": self.model,
            "model_available": self.model in models,
            "available_models": models,
        }


def build_model_client(settings) -> ModelClient:
    return OllamaClient(settings.ollama_url, settings.model)


def extract_sql(text: str) -> str:
    stripped = text.strip()
    if "```" not in stripped:
        return stripped.rstrip(";") + ";"

    parts = stripped.split("```")
    for part in parts:
        candidate = part.strip()
        if candidate.lower().startswith("sql"):
            candidate = candidate[3:].strip()
        if candidate.lower().startswith("select") or candidate.lower().startswith("with"):
            return candidate.rstrip(";") + ";"
    return stripped.rstrip(";") + ";"


def rows_for_prompt(rows: list[dict], max_rows: int = 20) -> str:
    return json.dumps(rows[:max_rows], default=str, indent=2)
