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

    def test_freshness_decays_continuously_to_zero(self) -> None:
        self.assertEqual(resetwatch.freshness_multiplier(30 * 60), 1.0)
        self.assertAlmostEqual(resetwatch.freshness_multiplier(75 * 60), 0.5)
        self.assertEqual(resetwatch.freshness_multiplier(120 * 60), 0.0)
        self.assertEqual(resetwatch.freshness_multiplier(121 * 60), 0.0)

    def test_source_age_rejects_far_future_timestamp(self) -> None:
        near_future = resetwatch.utc_now() + dt.timedelta(minutes=4)
        far_future = resetwatch.utc_now() + dt.timedelta(minutes=6)
        self.assertEqual(resetwatch.source_age_seconds(near_future), 0.0)
        self.assertIsNone(resetwatch.source_age_seconds(far_future))

    def test_history_filter_rejects_far_future_sample(self) -> None:
        now = 1_000_000.0
        rows = [
            {"sampled_at": now - 60},
            {"sampled_at": now + 4 * 60},
            {"sampled_at": now + 6 * 60},
            {"sampled_at": now - 15 * 24 * 3600},
        ]
        filtered = resetwatch.filter_history(rows, now)
        self.assertEqual(
            [row["sampled_at"] for row in filtered],
            [now - 60, now + 4 * 60],
        )

    def test_confirmed_event_also_receives_freshness_discount(self) -> None:
        original = resetwatch.fetch_json
        checked_at = resetwatch.utc_now() - dt.timedelta(minutes=75)

        def fake_fetch(url: str, timeout: float = 8.0) -> dict:
            if url == resetwatch.WILL_URL:
                raise RuntimeError("unavailable")
            return {
                "state": "yes",
                "updatedAt": int(checked_at.timestamp() * 1000),
                "automationSummary": {
                    "verdict": "reset_confirmed",
                    "mode": "active",
                },
            }

        resetwatch.fetch_json = fake_fetch
        try:
            signal = resetwatch.fetch_public_signal(None, False)
        finally:
            resetwatch.fetch_json = original
        self.assertEqual(signal["event_signal_raw"], 100.0)
        self.assertAlmostEqual(signal["event_signal_adjusted"], 50.0, delta=0.1)
        self.assertTrue(signal["signal_usable"])

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
        self.assertIn("重置卡：3 张，到期时间未知", text)
        self.assertIn("没算进上面的安全额度", text)

    def test_banked_reset_expiry_ignores_unavailable_credits(self) -> None:
        payload = {
            "signal": {"event_state": "unknown", "sources": []},
            "weekly": {
                "banked_resets": {
                    "available_count": 1,
                    "details": [
                        {"status": "redeemed", "expires_at": future(1)},
                        {"status": "available", "expires_at": future(48)},
                    ],
                },
            },
            "advice": {
                "status": "insufficient",
                "advice": "先别赌",
                "reason": "测试",
            },
        }
        text = resetwatch.render_text(payload)
        self.assertIn("最近一张约 2 天后到期", text)
        self.assertNotIn("最近一张约 1 小时后到期", text)

    def test_banked_reset_expiry_ignores_expired_available_credit(self) -> None:
        payload = {
            "signal": {"event_state": "unknown", "sources": []},
            "weekly": {
                "banked_resets": {
                    "available_count": 2,
                    "details": [
                        {"status": "available", "expires_at": future(-1)},
                        {"status": "available", "expires_at": future(48)},
                    ],
                },
            },
            "advice": {
                "status": "insufficient",
                "advice": "先别赌",
                "reason": "测试",
            },
        }
        text = resetwatch.render_text(payload)
        self.assertIn("最近一张约 2 天后到期", text)
        self.assertNotIn("已到期或正在结算", text)

    def test_banked_reset_expiry_requires_explicit_available_status(self) -> None:
        payload = {
            "signal": {"event_state": "unknown", "sources": []},
            "weekly": {
                "banked_resets": {
                    "available_count": 1,
                    "details": [{"expires_at": future(1)}],
                },
            },
            "advice": {
                "status": "insufficient",
                "advice": "先别赌",
                "reason": "测试",
            },
        }
        text = resetwatch.render_text(payload)
        self.assertIn("重置卡：1 张，到期时间未知", text)
        self.assertNotIn("最近一张", text)

    def test_zero_banked_count_wins_over_inconsistent_details(self) -> None:
        payload = {
            "signal": {"event_state": "unknown", "sources": []},
            "weekly": {
                "banked_resets": {
                    "available_count": 0,
                    "details": [
                        {"status": "available", "expires_at": future(1)},
                    ],
                },
            },
            "advice": {
                "status": "insufficient",
                "advice": "先别赌",
                "reason": "测试",
            },
        }
        text = resetwatch.render_text(payload)
        self.assertIn("重置卡：当前没有可用卡", text)
        self.assertNotIn("最近一张", text)

    def test_default_text_is_actionable_and_hides_implementation_terms(self) -> None:
        payload = {
            "signal": {
                "event_state": "unknown",
                "sources": [
                    {"name": "fresh", "status": "ok", "age_minutes": 10},
                    {"name": "old", "status": "stale", "age_minutes": 2760},
                ],
            },
            "weekly": {"banked_resets": {"available_count": 0, "details": []}},
            "advice": {
                "status": "ok",
                "advice": "收着蹬",
                "reason": "照当前速度，额度可能比正常周重置更早见底。",
                "weekly_used_percent": 30.0,
                "weekly_remaining_percent": 70.0,
                "hours_to_weekly_reset": 122.5,
                "expected_need_percent": 80.8,
                "safety_reserve_percent": 20.2,
                "event_signal_percent": 7.0,
                "combined_decision_score": 5.2,
                "signal_missing": False,
                "safe_headroom_percent": -30.9,
                "burn_estimation": "cold-start-average",
                "burn_samples": 0,
            },
        }
        text = resetwatch.render_text(payload)
        self.assertIn("今天怎么用：只跑今天非做不可的重任务", text)
        self.assertIn("离安全线还差 30.9%", text)
        self.assertIn("5 天 2 小时", text)
        self.assertIn("不当额度花", text)
        self.assertIn("（7%）", text)
        self.assertIn("剩 5.2%", text)
        self.assertNotIn("/100", text)
        self.assertIn("别把 Tibo 的心情当你的预算", text)
        self.assertNotIn("heuristic-score", text)
        self.assertNotIn("cold-start-average", text)

    def test_every_advice_level_has_its_own_voice(self) -> None:
        expected = {
            "猛猛蹬": "绿灯亮了",
            "可以加速蹬": "油门能再给一点",
            "稳着蹬": "这档速度刚好",
            "收着蹬": "别让额度提前下班",
            "先别赌": "账本缺页",
        }
        for label, phrase in expected.items():
            with self.subTest(label=label):
                self.assertIn(phrase, resetwatch.advice_title(label))

    def test_every_source_state_has_human_copy(self) -> None:
        expected = {
            "ok": "在线",
            "stale": "消息凉了",
            "error": "哨兵失联",
            "degraded": "带伤工作",
            "disabled": "没出门",
            "conflict": "口供对不上",
            "unhealthy": "打瞌睡",
            "unknown": "没说清",
        }
        for status, phrase in expected.items():
            with self.subTest(status=status):
                self.assertIn(phrase, resetwatch.source_status_copy(status))

    def test_confirmed_copy_does_not_claim_official_source(self) -> None:
        payload = {
            "signal": {"event_state": "confirmed", "sources": []},
            "weekly": {},
            "advice": {
                "status": "insufficient",
                "advice": "先别赌",
                "reason": "测试",
            },
        }
        text = resetwatch.render_text(payload)
        self.assertIn("公共来源确认事件发生", text)
        self.assertNotIn("官方信号已确认", text)

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
