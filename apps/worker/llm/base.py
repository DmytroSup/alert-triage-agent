"""LLM provider abstraction.

The worker never talks to a vendor SDK directly. It asks a provider for two
things - a classification and a summary - and every provider returns the same
shape. That keeps the vendor swappable and, more importantly, keeps the whole
repository runnable with no API key at all via the `mock` provider.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


VALID_CATEGORIES = ("infra", "security", "application", "network", "unknown")
VALID_SEVERITIES = ("P1", "P2", "P3", "P4")


@dataclass
class Classification:
    category: str = "unknown"
    severity: str = "P4"
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def sanitized(self) -> "Classification":
        """Never trust a model to stay inside the enum."""
        if self.category not in VALID_CATEGORIES:
            self.category = "unknown"
        if self.severity not in VALID_SEVERITIES:
            self.severity = "P4"
        self.reason = (self.reason or "")[:500]
        return self


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def classify(self, alert: dict[str, Any]) -> Classification:
        """Assign a category and a severity to a single normalized alert."""

    @abstractmethod
    def summarize(self, alerts: list[dict[str, Any]]) -> tuple[str, str]:
        """Return (title, summary) describing a group of correlated alerts."""

    # -- helpers shared by every concrete provider -------------------------

    @staticmethod
    def _classify_prompt(alert: dict[str, Any]) -> str:
        return (
            "You are triaging a monitoring alert. Respond with JSON only, "
            'shaped as {"category": ..., "severity": ..., "reason": ...}.\n'
            f"category must be one of {list(VALID_CATEGORIES)}.\n"
            f"severity must be one of {list(VALID_SEVERITIES)}, where P1 is a "
            "customer-facing outage and P4 is informational.\n"
            "reason is one short sentence.\n\n"
            f"Alert:\n{json.dumps(alert, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _summarize_prompt(alerts: list[dict[str, Any]]) -> str:
        return (
            "These monitoring alerts were correlated into a single incident. "
            "Respond with JSON only, shaped as "
            '{"title": ..., "summary": ...}.\n'
            "title: under 80 characters, states what is broken.\n"
            "summary: two sentences - what is happening and what to check "
            "first.\n\n"
            f"Alerts:\n{json.dumps(alerts, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Models like to wrap JSON in prose or fences. Dig it out."""
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return {}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}


def get_provider() -> LLMProvider:
    """Pick a provider from LLM_PROVIDER, defaulting to the offline mock."""
    choice = os.getenv("LLM_PROVIDER", "mock").lower()

    if choice == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider()
    if choice == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider()
    if choice == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()

    from .mock import MockProvider

    return MockProvider()
