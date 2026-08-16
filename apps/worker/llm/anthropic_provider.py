"""Anthropic Claude provider - REST API, no SDK."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import Classification, LLMProvider


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        if not self.api_key:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is unset"
            )

    def _call(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": 512,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        return data["content"][0]["text"]

    def classify(self, alert: dict[str, Any]) -> Classification:
        try:
            parsed = self._extract_json(self._call(self._classify_prompt(alert)))
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError) as e:
            return Classification("unknown", "P4", f"anthropic call failed: {e}")

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
