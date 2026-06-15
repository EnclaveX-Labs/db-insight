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


class GeminiClient(ModelClient):
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        try:
            response = httpx.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                params={"key": self.api_key},
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DbInsightError(f"Gemini request failed: {exc.response.text}") from exc
        except httpx.TimeoutException as exc:
            raise DbInsightError("Gemini took too long to respond.") from exc
        except httpx.HTTPError as exc:
            raise DbInsightError(f"Could not connect to Gemini: {exc}") from exc

        data = response.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DbInsightError(f"Gemini returned an unexpected response: {data}") from exc
        return "\n".join(str(part.get("text", "")) for part in parts).strip()

    def health(self) -> dict:
        try:
            response = httpx.get(
                f"{self.base_url}/models/{self.model}",
                params={"key": self.api_key},
                timeout=20,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DbInsightError(f"Gemini health check failed: {exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise DbInsightError(f"Could not connect to Gemini: {exc}") from exc

        return {"provider": "gemini", "model": self.model, "model_available": True}


def build_model_client(settings) -> ModelClient:
    if settings.model_provider == "gemini":
        if not settings.gemini_api_key:
            raise DbInsightError("GEMINI_API_KEY is required when using the Gemini provider.")
        return GeminiClient(settings.gemini_api_key, settings.gemini_model)
    if settings.model_provider == "ollama":
        return OllamaClient(settings.ollama_url, settings.model)
    raise DbInsightError(
        "DB_INSIGHT_MODEL_PROVIDER must be either 'ollama' or 'gemini'."
    )


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
