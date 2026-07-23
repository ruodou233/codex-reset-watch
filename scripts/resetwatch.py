#!/usr/bin/env python3
"""Local-first Codex reset signal and conservative quota advisor."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import select
import shutil
import subprocess
import sys
import time
import urllib.request
from typing import Any

VERSION = "0.1.0"
WILL_URL = "https://www.willcodexquotareset.com/api/forecast"
HAS_URL = "https://hascodexratelimitreset.today/api/status"
WEEK_MINUTES = 7 * 24 * 60
SOURCE_STALE_SECONDS = 2 * 60 * 60
SOURCE_FRESH_SECONDS = 30 * 60


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return dt.datetime.fromtimestamp(number, tz=dt.timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_time(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def clamp(value: float, low: float, high: float) -> float:
    if not math.isfinite(value):
        raise ValueError("value must be finite")
    return max(low, min(high, value))


def fetch_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"codex-reset-watch/{VERSION}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("response is not a JSON object")
    return payload


def source_age_seconds(timestamp: dt.datetime | None) -> float | None:
    if timestamp is None:
        return None
    return max(0.0, (utc_now() - timestamp).total_seconds())


def freshness_multiplier(age: float | None) -> float:
    if age is None or age > SOURCE_STALE_SECONDS:
        return 0.0
    if age <= SOURCE_FRESH_SECONDS:
        return 1.0
    span = SOURCE_STALE_SECONDS - SOURCE_FRESH_SECONDS
    return 0.5 + 0.5 * (SOURCE_STALE_SECONDS - age) / span


def fetch_public_signal(manual_probability: float | None, no_network: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_signal_raw": None,
        "event_signal_adjusted": None,
        "signal_kind": None,
        "signal_usable": False,
        "event_state": "unknown",
        "sources": [],
        "evidence_url": None,
    }
    if manual_probability is not None:
        probability = clamp(manual_probability, 0, 100)
        result.update(
            {
                "event_signal_raw": probability,
                "event_signal_adjusted": probability,
                "signal_kind": "manual-probability",
                "signal_usable": True,
                "event_state": "manual",
                "sources": [{"name": "manual", "status": "ok", "fresh": True}],
            }
        )
        return result
    if no_network:
        result["sources"].append({"name": "network", "status": "disabled", "fresh": False})
        return result

    will_score: float | None = None
    try:
        payload = fetch_json(WILL_URL)
        fetched_at = parse_time(payload.get("fetchedAt"))
        age = source_age_seconds(fetched_at)
        forecast = payload.get("forecast") or {}
        raw_score = forecast.get("score")
        if isinstance(raw_score, (int, float)):
            will_score = clamp(float(raw_score), 0, 100)
        source_errors = payload.get("sourceErrors")
        has_source_errors = bool(source_errors)
        usable = age is not None and age <= SOURCE_STALE_SECONDS and not has_source_errors
        announced = bool(forecast.get("resetAnnounced")) and usable
        result["sources"].append(
            {
                "name": "will-codex-reset",
                "status": "ok" if usable else ("degraded" if has_source_errors else "stale"),
                "fresh": usable,
                "fetched_at": iso(fetched_at),
                "age_minutes": round(age / 60, 1) if age is not None else None,
                "score": will_score,
                "upstream_error": has_source_errors,
            }
        )
        if announced:
            result["event_state"] = "announced"
            will_score = 100.0
        if will_score is not None:
            result["event_signal_raw"] = will_score
            result["event_signal_adjusted"] = round(
                will_score * freshness_multiplier(age), 2
            ) if usable else None
            result["signal_kind"] = "heuristic-score"
            result["signal_usable"] = usable
    except Exception as exc:
        result["sources"].append(
            {"name": "will-codex-reset", "status": "error", "fresh": False, "error": str(exc)}
        )

    try:
        payload = fetch_json(HAS_URL)
        summary = payload.get("automationSummary") or {}
        verdict = str(summary.get("verdict") or "").lower()
        state = str(payload.get("state") or "").lower()
        latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
        if state == "yes":
            checked_at = parse_time(payload.get("updatedAt"))
        elif verdict == "reset_confirmed":
            checked_at = parse_time(latest.get("checkedAt") or summary.get("checkedAt"))
        else:
            checked_at = parse_time(summary.get("checkedAt") or payload.get("updatedAt"))
        age = source_age_seconds(checked_at)
        mode = summary.get("mode")
        last_error = summary.get("lastError")
        healthy = not last_error and mode != "inactive"
        confirmed = (verdict == "reset_confirmed" or state == "yes") and healthy
        status = "ok"
        if last_error or mode == "inactive":
            status = "degraded"
        if age is None or age > SOURCE_STALE_SECONDS:
            status = "stale"
        result["sources"].append(
            {
                "name": "has-codex-reset",
                "status": status,
                "fresh": age is not None and age <= SOURCE_STALE_SECONDS and healthy,
                "checked_at": iso(checked_at),
                "age_minutes": round(age / 60, 1) if age is not None else None,
                "verdict": verdict or None,
                "mode": mode,
                "upstream_error": bool(last_error),
            }
        )
        tweet_url = summary.get("tweetUrl")
        if tweet_url and age is not None and age <= SOURCE_STALE_SECONDS and healthy:
            result["evidence_url"] = tweet_url
        if confirmed and age is not None and age <= SOURCE_STALE_SECONDS:
            result["event_state"] = "confirmed"
            result["event_signal_raw"] = 100.0
            result["event_signal_adjusted"] = 100.0
            result["signal_kind"] = "confirmed-event"
            result["signal_usable"] = True
        elif (
            result["event_state"] == "unknown"
            and verdict == "uncertain"
            and age is not None
            and age <= SOURCE_STALE_SECONDS
            and not last_error
        ):
            result["event_state"] = "uncertain"
    except Exception as exc:
        result["sources"].append(
            {"name": "has-codex-reset", "status": "error", "fresh": False, "error": str(exc)}
        )
    return result


def read_line(proc: subprocess.Popen[str], timeout: float) -> str:
    if proc.stdout is None:
        raise RuntimeError("app-server stdout unavailable")
    readable, _, _ = select.select([proc.stdout], [], [], timeout)
    if not readable:
        raise TimeoutError("timed out waiting for codex app-server")
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("codex app-server closed stdout")
    return line


def app_server_request(timeout: float = 10.0) -> dict[str, Any]:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("codex executable not found")
    proc = subprocess.Popen(
        [codex, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        if proc.stdin is None:
            raise RuntimeError("app-server stdin unavailable")

        def send(message: dict[str, Any]) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            proc.stdin.flush()

        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "codex-reset-watch", "version": VERSION},
                    "capabilities": {"experimentalApi": False},
                },
            }
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(read_line(proc, max(0.1, deadline - time.monotonic())))
            if message.get("id") == 1:
                if "error" in message:
                    raise RuntimeError(f"initialize failed: {message['error']}")
                break
        else:
            raise TimeoutError("initialize response timed out")
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read"})
        while time.monotonic() < deadline:
            message = json.loads(read_line(proc, max(0.1, deadline - time.monotonic())))
            if message.get("id") == 2:
                if "error" in message:
                    raise RuntimeError(f"rate limit request failed: {message['error']}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("rate limit response missing result")
                return result
        raise TimeoutError("rate limit response timed out")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def choose_weekly_window(payload: dict[str, Any]) -> dict[str, Any] | None:
    snapshots: list[dict[str, Any]] = []
    single = payload.get("rateLimits")
    if isinstance(single, dict):
        snapshots.append(single)
    by_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        snapshots.extend(value for value in by_id.values() if isinstance(value, dict))
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for snapshot in snapshots:
        for name in ("primary", "secondary"):
            window = snapshot.get(name)
            if not isinstance(window, dict):
                continue
            key = (
                window.get("windowDurationMins"),
                window.get("usedPercent"),
                window.get("resetsAt"),
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "bucket": name,
                    "limit_id": snapshot.get("limitId"),
                    "plan_type": snapshot.get("planType"),
                    "used_percent": window.get("usedPercent"),
                    "window_minutes": window.get("windowDurationMins"),
                    "resets_at": window.get("resetsAt"),
                }
            )
    valid = [
        c
        for c in candidates
        if isinstance(c.get("used_percent"), (int, float))
        and isinstance(c.get("window_minutes"), (int, float))
        and abs(float(c["window_minutes"]) - WEEK_MINUTES) <= 120
    ]
    if not valid:
        return None
    valid.sort(key=lambda c: c.get("window_minutes") or 0, reverse=True)
    return valid[0]


def history_path() -> pathlib.Path:
    if sys.platform == "darwin":
        root = pathlib.Path.home() / "Library" / "Application Support" / "codex-reset-watch"
    else:
        root = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local/share"))
        root = root / "codex-reset-watch"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / "snapshots.jsonl"


def load_history() -> list[dict[str, Any]]:
    path = history_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except Exception:
        return []
    cutoff = time.time() - 14 * 24 * 3600
    return [r for r in rows if isinstance(r.get("sampled_at"), (int, float)) and r["sampled_at"] >= cutoff]


def save_snapshot(weekly: dict[str, Any]) -> None:
    path = history_path()
    row = {
        "sampled_at": time.time(),
        "used_percent": weekly["used_percent"],
        "resets_at": weekly.get("resets_at"),
        "window_minutes": weekly.get("window_minutes"),
    }
    with path.open("a", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def percentile_nearest_rank(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("empty values")
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def estimate_burn(
    used_percent: float,
    resets_at: dt.datetime,
    window_minutes: float,
    history: list[dict[str, Any]],
) -> tuple[float, str, int]:
    rates: list[float] = []
    current_reset = resets_at.timestamp()
    rows = sorted(history, key=lambda row: row["sampled_at"])
    for right_index, right in enumerate(rows):
        if abs(float(right.get("resets_at") or 0) - current_reset) > 60:
            continue
        for left in reversed(rows[:right_index]):
            if left.get("resets_at") != right.get("resets_at"):
                continue
            hours = (right["sampled_at"] - left["sampled_at"]) / 3600
            if hours < 1:
                continue
            if hours > 24:
                break
            delta = float(right.get("used_percent", 0)) - float(left.get("used_percent", 0))
            if 0 <= delta <= 30:
                rates.append(delta / hours)
            break
    if rates:
        return percentile_nearest_rank(rates, 0.8), "history-p80", len(rates)
    hours_remaining = max(0.0, (resets_at - utc_now()).total_seconds() / 3600)
    elapsed = max(1.0, window_minutes / 60 - hours_remaining)
    return used_percent / elapsed, "cold-start-average", 0


def advice_label(headroom: float) -> tuple[str, str]:
    if headroom >= 20:
        return "猛猛蹬", "可以安排高消耗任务，但仍需保留安全垫。"
    if headroom >= 8:
        return "可以加速蹬", "可以提高使用强度，但不要一次性清空周额度。"
    if headroom >= -5:
        return "稳着蹬", "维持当前节奏，不要把尚未发生的临时重置当作确定额度。"
    return "收着蹬", "按保守预测，当前额度可能撑不到正常周重置。"


def calculate_advice(
    weekly: dict[str, Any],
    signal: dict[str, Any],
    arrival_probability: float,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    used = clamp(float(weekly["used_percent"]), 0, 100)
    remaining = 100 - used
    reset_at = parse_time(weekly.get("resets_at"))
    window_minutes = float(weekly.get("window_minutes") or WEEK_MINUTES)
    event_signal = signal.get("event_signal_adjusted")
    if reset_at is None:
        return {
            "status": "insufficient",
            "advice": "先别赌",
            "reason": "缺少正常周重置时间，无法估算到期前需求。",
        }
    hours_remaining = (reset_at - utc_now()).total_seconds() / 3600
    window_hours = window_minutes / 60
    if (
        not math.isfinite(window_hours)
        or window_hours <= 0
        or hours_remaining <= 0
        or hours_remaining > window_hours + 1
    ):
        return {
            "status": "insufficient",
            "advice": "先别赌",
            "reason": "正常周重置时间或窗口长度无效。",
        }
    burn_rate, burn_method, samples = estimate_burn(
        used, reset_at, window_minutes, history
    )
    need = clamp(burn_rate * hours_remaining, 0, 100)
    reserve = max(10.0, need * 0.25)
    if event_signal is None:
        event_signal = 0.0
        signal_missing = True
    else:
        signal_missing = not bool(signal.get("signal_usable"))
    arrival = clamp(arrival_probability, 0, 100)
    user_score = event_signal * arrival / 100
    weight = clamp((user_score - 65) / 30, 0, 1)
    reset_credit = min(used, 25.0) * weight
    headroom = remaining + reset_credit - need - reserve
    label, reason = advice_label(headroom)
    if signal_missing and label in {"猛猛蹬", "可以加速蹬"}:
        label = "稳着蹬"
        reason = "公共重置信号不可用；额度虽有余量，也不建议押注临时重置。"
    return {
        "status": "ok",
        "advice": label,
        "reason": reason,
        "weekly_used_percent": round(used, 2),
        "weekly_remaining_percent": round(remaining, 2),
        "weekly_resets_at": iso(reset_at),
        "hours_to_weekly_reset": round(hours_remaining, 2),
        "burn_rate_percent_per_hour": round(burn_rate, 4),
        "burn_estimation": burn_method,
        "burn_samples": samples,
        "expected_need_percent": round(need, 2),
        "safety_reserve_percent": round(reserve, 2),
        "event_signal_percent": round(float(event_signal), 2),
        "event_signal_kind": signal.get("signal_kind") or "unavailable",
        "arrival_discount_percent": round(arrival, 2),
        "combined_decision_score": round(user_score, 2),
        "signal_missing": signal_missing,
        "risk_weight": round(weight, 4),
        "risk_adjusted_reset_credit_percent": round(reset_credit, 2),
        "safe_headroom_percent": round(headroom, 2),
        "formula_version": "conservative-v2",
    }


def manual_weekly(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.weekly_used is None:
        return None
    reset_at = parse_time(args.weekly_reset_at)
    return {
        "bucket": "manual",
        "used_percent": clamp(args.weekly_used, 0, 100),
        "window_minutes": args.window_minutes,
        "resets_at": int(reset_at.timestamp()) if reset_at else None,
        "plan_type": args.plan,
        "source": "manual",
    }


def render_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    advice = payload.get("advice") or {}
    weekly = payload.get("weekly") or {}
    signal = payload.get("signal") or {}
    if advice.get("status") == "ok":
        lines.extend(
            [
                f"建议：{advice['advice']}",
                f"原因：{advice['reason']}",
                "",
                f"周额度：已用 {advice['weekly_used_percent']:.1f}%｜剩余 {advice['weekly_remaining_percent']:.1f}%",
                f"正常重置：{advice['hours_to_weekly_reset']:.1f} 小时后",
                f"预计需求：{advice['expected_need_percent']:.1f}%｜安全储备 {advice['safety_reserve_percent']:.1f}%",
                f"临时重置信号：{advice['event_signal_percent']:.1f}/100（{advice['event_signal_kind']}）"
                f" × 到账折扣 {advice['arrival_discount_percent']:.1f}%"
                f" = 决策分 {advice['combined_decision_score']:.1f}",
                f"风险折算额度：{advice['risk_adjusted_reset_credit_percent']:.1f}%｜安全余量 {advice['safe_headroom_percent']:.1f}%",
                f"消耗估计：{advice['burn_estimation']}（{advice['burn_samples']} 个历史区间）",
            ]
        )
        if advice.get("signal_missing"):
            lines.append("提示：公共信号不可用，本次不计入任何临时重置收益。")
    else:
        lines.extend(
            [
                f"建议：{advice.get('advice', '先别赌')}",
                f"原因：{advice.get('reason', '关键数据不足。')}",
            ]
        )
    banked = weekly.get("banked_resets")
    if isinstance(banked, dict):
        count = banked.get("available_count")
        lines.append(f"Banked reset：{count if count is not None else '未知'} 个（未自动计入建议）")
        expiries = [
            parse_time(item.get("expires_at"))
            for item in banked.get("details", [])
            if isinstance(item, dict) and item.get("expires_at") is not None
        ]
        expiries = [value for value in expiries if value is not None]
        if expiries:
            lines.append(f"最近到期：{iso(min(expiries))}")
        elif count:
            lines.append("到期信息：上游仅返回权威计数，未提供明细")
    if weekly.get("error"):
        lines.append(f"本机额度读取：失败（{weekly['error']}）")
    lines.append("")
    lines.append(f"公共信号：{signal.get('event_state', 'unknown')}")
    for source in signal.get("sources", []):
        detail = source.get("status", "unknown")
        age = source.get("age_minutes")
        if age is not None:
            detail += f"，{age:.1f} 分钟前"
        lines.append(f"- {source.get('name')}: {detail}")
    if signal.get("evidence_url"):
        lines.append(f"证据：{signal['evidence_url']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Codex quota and produce a conservative usage recommendation."
    )
    subparsers = parser.add_subparsers(dest="command")
    check = subparsers.add_parser("check", help="fetch signal, read local quota, and advise")
    check.add_argument("--weekly-used", type=float, help="manual weekly used percentage")
    check.add_argument("--weekly-reset-at", help="manual weekly reset ISO time or Unix seconds")
    check.add_argument("--window-minutes", type=float, default=WEEK_MINUTES)
    check.add_argument("--plan", default="unknown")
    check.add_argument("--probability", type=float, help="manual 48h event probability")
    check.add_argument(
        "--arrival-probability",
        type=float,
        default=75.0,
        help="conservative chance that the event covers this account (default: 75)",
    )
    check.add_argument("--no-network", action="store_true")
    check.add_argument("--no-local", action="store_true")
    check.add_argument("--json", action="store_true")
    parser.set_defaults(command="check")
    return parser


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["check", *argv]
    args = parser.parse_args(argv)
    if args.command != "check":
        parser.error("unsupported command")
    signal = fetch_public_signal(args.probability, args.no_network)
    weekly = manual_weekly(args)
    weekly_meta: dict[str, Any] = {}
    if weekly is None and not args.no_local:
        try:
            local_payload = app_server_request()
            weekly = choose_weekly_window(local_payload)
            if weekly is None:
                raise RuntimeError("no rate-limit window returned")
            weekly["source"] = "codex-app-server"
            credits = local_payload.get("rateLimitResetCredits")
            if isinstance(credits, dict):
                details = []
                for credit in credits.get("credits") or []:
                    if not isinstance(credit, dict):
                        continue
                    details.append(
                        {
                            "title": credit.get("title"),
                            "status": credit.get("status"),
                            "reset_type": credit.get("resetType"),
                            "granted_at": credit.get("grantedAt"),
                            "expires_at": credit.get("expiresAt"),
                        }
                    )
                weekly["banked_resets"] = {
                    "available_count": credits.get("availableCount"),
                    "details": details,
                }
        except Exception as exc:
            weekly_meta["error"] = str(exc)
    history = load_history()
    advice: dict[str, Any]
    if weekly is None:
        advice = {
            "status": "insufficient",
            "advice": "先别赌",
            "reason": "无法读取周额度；请提供 --weekly-used 和 --weekly-reset-at。",
        }
    else:
        working_history = list(history)
        if weekly.get("resets_at") is not None:
            working_history.append(
                {
                    "sampled_at": time.time(),
                    "used_percent": weekly["used_percent"],
                    "resets_at": weekly.get("resets_at"),
                    "window_minutes": weekly.get("window_minutes"),
                }
            )
        advice = calculate_advice(
            weekly, signal, args.arrival_probability, working_history
        )
        if weekly.get("source") == "codex-app-server":
            save_snapshot(weekly)
    payload = {
        "version": VERSION,
        "generated_at": iso(utc_now()),
        "signal": signal,
        "weekly": {**(weekly or {}), **weekly_meta},
        "advice": advice,
        "disclaimer": "保守决策辅助，不保证临时重置或个人到账。",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(payload))
    return 0 if advice.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
