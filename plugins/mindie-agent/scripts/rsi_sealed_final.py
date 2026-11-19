#!/usr/bin/env python3
"""Validate the independent, paper-style RSI sealed-final evidence.

[Role] Checks disjoint unseen prompts, long outputs, strict token forcing, and
       paired speed confidence intervals before a candidate can be released.
[Invariants] Missing evidence fails closed; an asserted boolean cannot replace
             the underlying rows when local validation is requested.
[Timeouts & Locks] Pure deterministic statistics; no service or NPU is called.
[Side-Effect Policy] Returns a report and never changes candidate state.
"""
from __future__ import annotations

import hashlib
import math
import random
from typing import Any, Mapping, Sequence

try:
    from rsi_evidence import validate_paired, validate_teacher_forcing
except ImportError:  # pragma: no cover - direct package execution fallback
    from .rsi_evidence import validate_paired, validate_teacher_forcing

SCHEMA = "mindie-agent/rsi-sealed-final/v1"


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _key(row: Mapping[str, Any]) -> str:
    for name in ("id", "prompt_id", "prompt", "input"):
        if row.get(name) not in (None, ""):
            return str(row[name])
    return "row:" + hashlib.sha256(repr(sorted((str(k), repr(v)) for k, v in row.items())).encode()).hexdigest()


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def bootstrap_ci(values: Sequence[float], confidence: float = 0.95,
                 iterations: int = 2000) -> tuple[float, float] | None:
    if not values:
        return None
    if len(values) == 1:
        value = float(values[0])
        return value, value
    rng = random.Random(0)
    samples = []
    for _ in range(max(100, iterations)):
        samples.append(sum(rng.choice(values) for _ in values) / len(values))
    samples.sort()
    alpha = (1.0 - confidence) / 2.0
    low = samples[max(0, min(len(samples) - 1, math.floor(alpha * len(samples))))]
    high = samples[max(0, min(len(samples) - 1, math.ceil((1 - alpha) * len(samples)) - 1))]
    return float(low), float(high)


def _speedups(payload: Mapping[str, Any]) -> list[float]:
    direct = payload.get("paired_speedups")
    if isinstance(direct, list):
        return [float(item) for item in direct if isinstance(item, (int, float))]
    result = []
    for row in _rows(payload.get("paired_runs")):
        candidate = row.get("candidate_time_s", row.get("candidate_latency_s"))
        baseline = row.get("baseline_time_s", row.get("baseline_latency_s"))
        if isinstance(candidate, (int, float)) and isinstance(baseline, (int, float)) and candidate > 0:
            result.append(float(baseline) / float(candidate))
    return result


def _token_gate(payload: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    rows = _rows(payload.get("token_forcing_rows"))
    if not rows:
        return False, {"passed": False, "reason": "token_forcing_rows_missing", "positions": 0}
    mismatches = sum(1 for row in rows if row.get("mismatch") is True
                     or row.get("match") is False
                     or row.get("token_match") is False)
    forced = all(row.get("teacher_forced") is True for row in rows)
    exact = mismatches == 0
    passed = forced and exact
    return passed, {"passed": passed, "teacher_forced": forced,
                    "mismatches": mismatches, "positions": len(rows),
                    "reason": "ok" if passed else "strict_token_forcing_failed"}


def evaluate(payload: Mapping[str, Any]) -> dict[str, Any]:
    unseen = _rows(payload.get("unseen_prompts"))
    long_rows = _rows(payload.get("long_outputs"))
    unseen_keys = {_key(row) for row in unseen}
    long_keys = {_key(row) for row in long_rows}
    min_unseen = max(1, int(payload.get("min_unseen_prompts", 3)))
    min_long = max(1, int(payload.get("min_long_prompts", 1)))
    min_tokens = max(1, int(payload.get("min_long_tokens", 1024)))
    long_tokens = [_integer(row.get("output_tokens", row.get("generated_tokens", row.get("tokens", 0))))
                   for row in long_rows]
    speedups = _speedups(payload)
    ci = bootstrap_ci(speedups)
    lower = ci[0] if ci is not None else None
    token_passed, token_report = _token_gate(payload)
    min_paired_runs = max(1, int(payload.get("min_paired_runs", 5)))
    paired_evidence = validate_paired(
        {"paired_runs": payload.get("paired_runs"), "paired_speedups": speedups},
        min_runs=min_paired_runs,
    )
    teacher_evidence = validate_teacher_forcing(
        {"token_forcing_rows": payload.get("token_forcing_rows")},
        min_rows=max(1, int(payload.get("min_teacher_forcing_rows", 1))),
    )
    exploration_keys = {_key(row) for row in _rows(payload.get("exploration_prompts"))}
    disjoint = not ((unseen_keys | long_keys) & exploration_keys) and not (unseen_keys & long_keys)
    gates = {
        "unseen_prompts": len(unseen) >= min_unseen,
        "long_outputs": len(long_rows) >= min_long and bool(long_tokens)
        and max(long_tokens) >= min_tokens,
        "holdout_disjoint": disjoint,
        "strict_teacher_forcing": token_passed and teacher_evidence["passed"],
        "paired_speed_ci": paired_evidence["passed"] and lower is not None
        and lower > float(payload.get("min_speedup", 1.0)),
    }
    failures = [name for name, passed in gates.items() if not passed]
    return {
        "schema": SCHEMA,
        "passed": not failures,
        "gates": gates,
        "failure_reasons": failures,
        "unseen_count": len(unseen),
        "long_count": len(long_rows),
        "long_max_tokens": max(long_tokens) if long_tokens else 0,
        "paired_speedups": speedups,
        "paired_speedup_ci": {"lower": ci[0], "upper": ci[1]} if ci else None,
        "paired_speedup_ci_lower_bound": lower,
        "token_forcing": token_report,
        "evidence": {"paired_performance": paired_evidence,
                     "teacher_forcing": teacher_evidence},
    }
