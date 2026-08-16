"""Google Gemini provider - uses the REST API directly, no SDK."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import Classification, LLMProvider


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        if not self.api_key:
            raise RuntimeError("LLM_PROVIDER=gemini but GEMINI_API_KEY is unset")

    def _call(self, prompt: str) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        body = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            }
        ).encode()

        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        return data["candidates"][0]["content"]["parts"][0]["text"]

    def classify(self, alert: dict[str, Any]) -> Classification:
        try:
            parsed = self._extract_json(self._call(self._classify_prompt(alert)))
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError) as e:
            return Classification("unknown", "P4", f"gemini call failed: {e}")

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
