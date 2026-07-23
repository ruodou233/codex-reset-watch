#!/usr/bin/env python3
"""Deterministic tests for resetwatch.py."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import time
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("resetwatch.py")
SPEC = importlib.util.spec_from_file_location("resetwatch", MODULE_PATH)
assert SPEC and SPEC.loader
resetwatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resetwatch)


def future(hours: float) -> int:
    return int(time.time() + hours * 3600)


class ResetWatchTests(unittest.TestCase):
    def weekly(self, used: float = 70, hours: float = 48) -> dict:
        return {
            "used_percent": used,
            "resets_at": future(hours),
            "window_minutes": resetwatch.WEEK_MINUTES,
        }

    def test_confirmed_signal_affects_default_model(self) -> None:
        signal = {
            "event_signal_adjusted": 100.0,
            "signal_kind": "confirmed-event",
            "signal_usable": True,
        }
        result = resetwatch.calculate_advice(self.weekly(), signal, 75.0, [])
        self.assertGreater(result["risk_adjusted_reset_credit_percent"], 0)
        self.assertEqual(result["formula_version"], "conservative-v2")

    def test_missing_signal_cannot_recommend_full_speed(self) -> None:
        signal = {
            "event_signal_adjusted": None,
            "signal_kind": None,
            "signal_usable": False,
        }
        result = resetwatch.calculate_advice(
            self.weekly(used=5, hours=2), signal, 75.0, []
        )
        self.assertIn(result["advice"], {"稳着蹬", "收着蹬"})
        self.assertTrue(result["signal_missing"])

    def test_weekly_bucket_is_matched_not_longest(self) -> None:
        payload = {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 10,
                        "windowDurationMins": resetwatch.WEEK_MINUTES,
                        "resetsAt": future(24),
                    },
                    "secondary": {
                        "usedPercent": 20,
                        "windowDurationMins": 30 * 24 * 60,
                        "resetsAt": future(100),
                    },
                }
            }
        }
        weekly = resetwatch.choose_weekly_window(payload)
        self.assertIsNotNone(weekly)
        self.assertEqual(weekly["window_minutes"], resetwatch.WEEK_MINUTES)

    def test_five_hour_only_is_not_called_weekly(self) -> None:
        payload = {
            "rateLimits": {
                "primary": {
                    "usedPercent": 10,
                    "windowDurationMins": 300,
                    "resetsAt": future(2),
                }
            }
        }
        self.assertIsNone(resetwatch.choose_weekly_window(payload))

    def test_current_sample_participates_in_burn_estimate(self) -> None:
        reset_at = dt.datetime.fromtimestamp(future(48), tz=dt.timezone.utc)
        previous = {
            "sampled_at": time.time() - 2 * 3600,
            "used_percent": 10,
            "resets_at": int(reset_at.timestamp()),
            "window_minutes": resetwatch.WEEK_MINUTES,
        }
        current = {
            "sampled_at": time.time(),
            "used_percent": 20,
            "resets_at": int(reset_at.timestamp()),
            "window_minutes": resetwatch.WEEK_MINUTES,
        }
        rate, method, samples = resetwatch.estimate_burn(
            20, reset_at, resetwatch.WEEK_MINUTES, [previous, current]
        )
        self.assertAlmostEqual(rate, 5.0, places=2)
        self.assertEqual(method, "history-p80")
        self.assertEqual(samples, 1)

    def test_expired_reset_time_is_rejected(self) -> None:
        signal = {
            "event_signal_adjusted": 100.0,
            "signal_kind": "confirmed-event",
            "signal_usable": True,
        }
        result = resetwatch.calculate_advice(
            self.weekly(hours=-1), signal, 75.0, []
        )
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["advice"], "先别赌")

    def test_banked_reset_count_is_visible_without_details(self) -> None:
        payload = {
            "signal": {"event_state": "unknown", "sources": []},
            "weekly": {
                "banked_resets": {"available_count": 3, "details": []},
            },
            "advice": {
                "status": "insufficient",
                "advice": "先别赌",
                "reason": "测试",
            },
        }
        text = resetwatch.render_text(payload)
        self.assertIn("Banked reset：3 个", text)
        self.assertIn("未提供明细", text)

    def test_source_errors_disable_will_score(self) -> None:
        original = resetwatch.fetch_json

        def fake_fetch(url: str, timeout: float = 8.0) -> dict:
            if url == resetwatch.WILL_URL:
                return {
                    "fetchedAt": resetwatch.iso(resetwatch.utc_now()),
                    "forecast": {"score": 90, "resetAnnounced": True},
                    "sourceErrors": {"x": "down"},
                }
            return {
                "state": "no",
                "updatedAt": int(time.time() * 1000),
                "automationSummary": {
                    "checkedAt": int(time.time() * 1000),
                    "verdict": "uncertain",
                    "mode": "inactive",
                    "lastError": "down",
                },
            }

        resetwatch.fetch_json = fake_fetch
        try:
            signal = resetwatch.fetch_public_signal(None, False)
        finally:
            resetwatch.fetch_json = original
        self.assertIsNone(signal["event_signal_adjusted"])
        self.assertFalse(signal["signal_usable"])
        self.assertNotEqual(signal["event_state"], "announced")


if __name__ == "__main__":
    unittest.main()
