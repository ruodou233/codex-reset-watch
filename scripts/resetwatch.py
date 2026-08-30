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

VERSION = "0.2.0"
CODEX_RESETS_URL = "https://codex-resets.com/api/v1/status"
WEEK_MINUTES = 7 * 24 * 60
SOURCE_STALE_SECONDS = 2 * 60 * 60
SOURCE_FRESH_SECONDS = 30 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60


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
    age = (utc_now() - timestamp).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS:
        return None
    return max(0.0, age)


def freshness_multiplier(age: float | None) -> float:
    if age is None or age > SOURCE_STALE_SECONDS:
        return 0.0
    if age <= SOURCE_FRESH_SECONDS:
        return 1.0
    span = SOURCE_STALE_SECONDS - SOURCE_FRESH_SECONDS
    return (SOURCE_STALE_SECONDS - age) / span


def numeric_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 100:
        return None
    return number


def source_url(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    source = record.get("source")
    if not isinstance(source, dict):
        return None
    url = source.get("url")
    return url if isinstance(url, str) and url else None


def fetch_public_signal(manual_probability: float | None, no_network: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_signal_raw": None,
        "event_signal_adjusted": None,
        "signal_kind": None,
        "signal_usable": False,
        "selected_source": None,
        "event_state": "unknown",
        "event_expires_at": None,
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
                "selected_source": "manual",
                "event_state": "manual",
                "sources": [{"name": "manual", "status": "ok", "fresh": True}],
            }
        )
        return result
    if no_network:
        result["sources"].append({"name": "network", "status": "disabled", "fresh": False})
        return result

    try:
        payload = fetch_json(CODEX_RESETS_URL)
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        generated_at = parse_time(meta.get("generated_at"))
        generated_age = source_age_seconds(generated_at)
        healthy = generated_age is not None and generated_age <= SOURCE_STALE_SECONDS

        watch = data.get("active_watch") if isinstance(data.get("active_watch"), dict) else None
        latest = data.get("latest_reset") if isinstance(data.get("latest_reset"), dict) else None
        observed_at = parse_time(watch.get("observed_at")) if watch else None
        expires_at = parse_time(watch.get("expires_at")) if watch else None
        score = numeric_score(watch.get("reset_chance_percent")) if watch else None
        watch_url = source_url(watch)
        latest_at = parse_time(latest.get("announced_at")) if latest else None
        latest_type = latest.get("reset_type") if latest else None
        latest_age = source_age_seconds(latest_at)
        latest_url = source_url(latest)
        now = utc_now()
        watch_valid = bool(
            healthy
            and score is not None
            and watch_url is not None
            and observed_at is not None
            and expires_at is not None
            and observed_at <= now
            and observed_at < expires_at
            and now < expires_at
        )
        latest_fresh = bool(
            healthy
            and latest_type == "regular"
            and latest_url is not None
            and latest_age is not None
            and latest_age <= SOURCE_STALE_SECONDS
        )

        status = "ok" if healthy else "stale"
        rejection_reason = None
        if not healthy:
            rejection_reason = "api-stale-or-invalid-generated-at"
        elif watch and not watch_valid and not latest_fresh:
            rejection_reason = "watch-expired-or-invalid"
        elif not watch_valid and not latest_fresh:
            rejection_reason = "no-active-signal"
        result["sources"].append(
            {
                "name": "codex-resets",
                "status": status,
                "fresh": healthy,
                "generated_at": iso(generated_at),
                "age_minutes": round(generated_age / 60, 1) if generated_age is not None else None,
                "score": score,
                "level": watch.get("level") if watch else None,
                "forecast_window": watch.get("forecast_window") if watch else None,
                "observed_at": iso(observed_at),
                "expires_at": iso(expires_at),
                "latest_reset_at": iso(latest_at),
                "latest_reset_type": latest_type,
                "source_url": watch_url or latest_url,
                "rejection_reason": rejection_reason,
            }
        )

        newer_confirmation = bool(
            latest_fresh
            and latest_at is not None
            and (observed_at is None or latest_at >= observed_at)
        )
        if newer_confirmation:
            result["event_signal_raw"] = 100.0
            result["event_signal_adjusted"] = round(
                100.0 * freshness_multiplier(latest_age), 2
            )
            result["signal_kind"] = "confirmed-event"
            result["signal_usable"] = True
            result["selected_source"] = "codex-resets"
            result["event_state"] = "confirmed"
            result["event_expires_at"] = iso(
                latest_at + dt.timedelta(seconds=SOURCE_STALE_SECONDS)
            )
            result["evidence_url"] = latest_url
        elif watch_valid and score is not None:
            result["event_signal_raw"] = score
            result["event_signal_adjusted"] = score
            result["signal_kind"] = "heuristic-score"
            result["signal_usable"] = True
            result["selected_source"] = "codex-resets"
            result["event_state"] = "likely"
            result["event_expires_at"] = iso(expires_at)
            result["evidence_url"] = watch_url
    except Exception as exc:
        result["sources"].append(
            {"name": "codex-resets", "status": "error", "fresh": False, "error": str(exc)}
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


def filter_history(
    rows: list[dict[str, Any]], now: float | None = None
) -> list[dict[str, Any]]:
    current = time.time() if now is None else now
    cutoff = current - 14 * 24 * 3600
    future_limit = current + MAX_FUTURE_SKEW_SECONDS
    return [
        row
        for row in rows
        if (
            isinstance(row.get("sampled_at"), (int, float))
            and cutoff <= row["sampled_at"] <= future_limit
        )
    ]


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
    return filter_history(rows)


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
        return "猛猛蹬", "额度跑在消耗前面，可以安排高消耗任务。"
    if headroom >= 8:
        return "可以加速蹬", "额度还有余量，可以把使用强度提一档。"
    if headroom >= -5:
        return "稳着蹬", "当前节奏基本合适，先别追着重置风声加速。"
    return "收着蹬", "照当前速度，额度可能比正常周重置更早见底。"


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


def human_duration(hours: float) -> str:
    total_minutes = max(0, round(hours * 60))
    days, remainder = divmod(total_minutes, 24 * 60)
    hours_part, minutes = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} 天")
    if hours_part:
        parts.append(f"{hours_part} 小时")
    if not days and minutes:
        parts.append(f"{minutes} 分钟")
    return " ".join(parts) or "不到 1 分钟"


def advice_action(label: str, reserve: float | None = None) -> str:
    reserve_copy = (
        f"至少留住 {reserve:.1f}% 的安全垫。" if reserve is not None else ""
    )
    actions = {
        "猛猛蹬": f"高消耗任务可以上场，{reserve_copy}",
        "可以加速蹬": f"可以多开一个重任务，{reserve_copy}",
        "稳着蹬": f"维持现在的任务强度，{reserve_copy}",
        "收着蹬": f"只跑今天非做不可的重任务，其余先排队；{reserve_copy}",
        "先别赌": "关键账目没算齐，先别为传闻追加高消耗任务。",
    }
    return actions.get(label, "先按当前节奏使用，别把传闻当成到账额度。")


def advice_title(label: str) -> str:
    return {
        "猛猛蹬": "猛猛蹬｜绿灯亮了，重任务排队上车",
        "可以加速蹬": "可以加速蹬｜油门能再给一点，但别一脚到底",
        "稳着蹬": "稳着蹬｜这档速度刚好，别追着风声跑",
        "收着蹬": "收着蹬｜先松油门，别让额度提前下班",
        "先别赌": "先别赌｜账本缺页，今天不押重置",
    }.get(label, f"{label}｜先看清账本再动")


def signal_strength(score: float, missing: bool = False) -> str:
    if missing:
        return "不可用"
    if score >= 90:
        return "很强"
    if score >= 60:
        return "中等"
    if score >= 30:
        return "偏弱"
    return "很弱"


def source_status_copy(status: str) -> str:
    return {
        "ok": "在线",
        "stale": "这条消息凉了",
        "error": "哨兵失联",
        "degraded": "带伤工作，先不采信",
        "disabled": "今天没出门",
        "conflict": "两边口供对不上",
        "unhealthy": "上游在打瞌睡",
        "unknown": "状态没说清",
    }.get(status, status)


def render_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    advice = payload.get("advice") or {}
    weekly = payload.get("weekly") or {}
    signal = payload.get("signal") or {}
    if advice.get("status") == "ok":
        label = advice["advice"]
        headroom = float(advice["safe_headroom_percent"])
        gap_copy = (
            f"到正常重置还能多出 {headroom:.1f}%"
            if headroom >= 0
            else f"离安全线还差 {abs(headroom):.1f}%"
        )
        burn_copy = (
            "历史还不够，先按本周期平均速度估"
            if advice.get("burn_estimation") == "cold-start-average"
            else f"参考 {advice.get('burn_samples', 0)} 个近期消耗区间"
        )
        score = float(advice["event_signal_percent"])
        combined = float(advice["combined_decision_score"])
        lines.extend(
            [
                advice_title(label),
                f"今天怎么用：{advice_action(label, advice['safety_reserve_percent'])}",
                f"为什么：{advice['reason']}",
                f"这笔账：{gap_copy}；正常重置还有 {human_duration(advice['hours_to_weekly_reset'])}。",
                "",
                f"周额度：剩 {advice['weekly_remaining_percent']:.1f}%（已用 {advice['weekly_used_percent']:.1f}%）",
                f"续航：预计还要 {advice['expected_need_percent']:.1f}% + 安全垫 {advice['safety_reserve_percent']:.1f}%",
                f"重置风声：{signal_strength(score, advice.get('signal_missing', False))}"
                f"（{score:.0f}%）｜按你的到账折扣剩 {combined:.1f}%，不当额度花",
                f"速度依据：{burn_copy}",
            ]
        )
        if advice.get("signal_missing"):
            lines.append("护栏：公共风声不可用，本次临时重置收益按 0 计算。")
        event_expires_at = parse_time(signal.get("event_expires_at"))
        if event_expires_at and event_expires_at > utc_now():
            expiry_hours = (event_expires_at - utc_now()).total_seconds() / 3600
            lines.append(f"风声窗口：约 {human_duration(expiry_hours)}后到期")
    else:
        label = advice.get("advice", "先别赌")
        lines.extend(
            [
                advice_title(label),
                f"今天怎么用：{advice_action(label)}",
                f"卡在哪：{advice.get('reason', '关键数据不足。')}",
            ]
        )
    banked = weekly.get("banked_resets")
    if isinstance(banked, dict):
        count = banked.get("available_count")
        expiries = [
            parse_time(item.get("expires_at"))
            for item in banked.get("details", [])
            if (
                isinstance(item, dict)
                and item.get("expires_at") is not None
                and str(item.get("status") or "").lower() == "available"
            )
        ]
        now = utc_now()
        expiries = [value for value in expiries if value is not None and value > now]
        if count == 0:
            lines.append("重置卡：当前没有可用卡")
        elif expiries:
            expiry_hours = (min(expiries) - now).total_seconds() / 3600
            expiry_copy = f"最近一张约 {human_duration(expiry_hours)}后到期"
            lines.append(
                f"重置卡：{count if count is not None else '未知'} 张，{expiry_copy}；没算进上面的安全额度"
            )
        elif count:
            lines.append(f"重置卡：{count} 张，到期时间未知；没算进上面的安全额度")
        else:
            lines.append("重置卡：数量和到期时间都未知")
    if weekly.get("error"):
        lines.append(f"本机账本：门没打开，额度没读到（{weekly['error']}）")
    lines.append("")
    event_state = signal.get("event_state", "unknown")
    event_copy = {
        "confirmed": "公共来源确认事件发生，等你账户自己报到",
        "announced": "Tibo 那边有动静，但你账户还没报到",
        "manual": "采用你手动给的风声",
        "uncertain": "线索有点虚，先按没重置算",
        "likely": "风声在路上，还没到账",
        "unlikely": "今天没听见按钮响",
        "conflict": "两边口供对不上，按没重置算",
        "unknown": "还没等到可信口供",
    }.get(event_state, event_state)
    lines.append(f"公共哨兵：{event_copy}。别把 Tibo 的心情当你的预算。")
    for source in signal.get("sources", []):
        detail = source.get("status", "unknown")
        age = source.get("age_minutes")
        if age is not None:
            detail = f"{source_status_copy(detail)}，{human_duration(age / 60)}前更新"
        else:
            detail = source_status_copy(detail)
        lines.append(f"- {source.get('name')}：{detail}")
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
