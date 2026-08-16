"""OpenAI-compatible provider - works with OpenAI and any drop-in gateway."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import Classification, LLMProvider


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        ).rstrip("/")
        if not self.api_key:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is unset")

    def _call(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        return data["choices"][0]["message"]["content"]

    def classify(self, alert: dict[str, Any]) -> Classification:
        try:
            parsed = self._extract_json(self._call(self._classify_prompt(alert)))
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError) as e:
            return Classification("unknown", "P4", f"openai call failed: {e}")

        return Classification(
            category=parsed.get("category", "unknown"),
            severity=parsed.get("severity", "P4"),
            reason=parsed.get("reason", ""),
            raw=parsed,
        ).sanitized()

    def summarize(self, alerts: list[dict[str, Any]]) -> tuple[str, str]:
        try:
            parsed = self._extract_json(
                self._call(self._summarize_prompt(alerts))
            )
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError):
            parsed = {}

        title = parsed.get("title") or "Correlated incident"
        summary = parsed.get("summary") or f"{len(alerts)} correlated alerts."
        return title[:80], summary[:800]
