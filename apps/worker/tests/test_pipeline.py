"""Offline tests for the pure parts of the pipeline.

No database and no network, so `python -m unittest` runs them anywhere:

    cd apps/worker && python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm.mock import MockProvider  # noqa: E402
from normalizer import correlation_key, normalize  # noqa: E402


class TestNormalizer(unittest.TestCase):
    def test_prometheus_shape(self):
        raw = {
            "labels": {"instance": "web-01", "job": "checkout", "alertname": "HighErrorRate"},
            "annotations": {"description": "error rate above 5%"},
            "startsAt": "2026-08-16T10:00:00Z",
        }
        n = normalize("prometheus", raw)
        self.assertEqual(n["host"], "web-01")
        self.assertEqual(n["service"], "checkout")
        self.assertEqual(n["check"], "HighErrorRate")
        self.assertEqual(n["message"], "error rate above 5%")
        self.assertEqual(n["timestamp"], "2026-08-16T10:00:00Z")

    def test_zabbix_shape(self):
        raw = {
            "hostname": "db-primary",
            "priority": "critical",
            "text": "disk space below 5%",
        }
        n = normalize("zabbix", raw)
        self.assertEqual(n["host"], "db-primary")
        self.assertEqual(n["severity"], "critical")

    def test_case_insensitive_keys(self):
        n = normalize("custom", {"HOST": "edge-3", "Message": "link down"})
        self.assertEqual(n["host"], "edge-3")
        self.assertEqual(n["message"], "link down")

    def test_missing_fields_are_none_not_crash(self):
        n = normalize("weird", {"foo": "bar"})
        self.assertIsNone(n["host"])
        self.assertIsNotNone(n["timestamp"])
        self.assertTrue(n["message"])

    def test_message_is_synthesised_when_absent(self):
        n = normalize("custom", {"host": "api-2", "alertname": "PodCrashLoop"})
        self.assertIn("PodCrashLoop", n["message"])

    def test_correlation_key_is_order_stable(self):
        n = {"host": "web-01", "category": "infra", "service": "checkout"}
        self.assertEqual(correlation_key(n, ["category", "host"]), "infra|web-01")

    def test_correlation_key_keeps_empty_segments(self):
        # Missing fields must not silently collapse two different alerts.
        a = correlation_key({"host": "web-01"}, ["category", "host"])
        b = correlation_key({"host": "web-02"}, ["category", "host"])
        self.assertNotEqual(a, b)
        self.assertEqual(a, "|web-01")


class TestMockClassifier(unittest.TestCase):
    def setUp(self):
        self.p = MockProvider()

    def test_security_alert(self):
        c = self.p.classify({"message": "unauthorized login attempt blocked by firewall"})
        self.assertEqual(c.category, "security")

    def test_infra_alert(self):
        c = self.p.classify({"message": "disk usage on node exceeded threshold"})
        self.assertEqual(c.category, "infra")

    def test_network_alert(self):
        c = self.p.classify({"message": "packet loss and dns timeout on interface"})
        self.assertEqual(c.category, "network")

    def test_declared_severity_wins(self):
        c = self.p.classify({"message": "slow queries", "severity": "critical"})
        self.assertEqual(c.severity, "P1")

    def test_severity_falls_back_to_keywords(self):
        c = self.p.classify({"message": "service is down, complete outage"})
        self.assertEqual(c.severity, "P1")

    def test_output_always_inside_enum(self):
        c = self.p.classify({"message": "something entirely unclassifiable"})
        self.assertIn(c.category, ("infra", "security", "application", "network", "unknown"))
        self.assertIn(c.severity, ("P1", "P2", "P3", "P4"))

    def test_summarize_returns_title_and_summary(self):
        title, summary = self.p.summarize(
            [{"host": "web-01", "category": "infra", "message": "cpu high", "timestamp": "t"}]
        )
        self.assertTrue(title)
        self.assertLessEqual(len(title), 80)
        self.assertIn("web-01", summary)


if __name__ == "__main__":
    unittest.main()
