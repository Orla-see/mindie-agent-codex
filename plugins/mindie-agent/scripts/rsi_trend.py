#!/usr/bin/env python3
"""Trend-based stopping for adaptive RSI parameter search."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def _number(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def assess_bound(observations: Sequence[Mapping[str, Any]],
                 parameter: str = "num_speculative_tokens",
                 metric: str = "throughput_tokens_s",
                 *, plateau_tolerance: float = 0.01,
                 regression_tolerance: float = 0.02,
                 utilization_threshold: float = 0.90) -> dict[str, Any]:
    """Assess whether increasing a parameter has reached a measured bound.

    Rows are sorted by the parameter, so the controller can probe a wider
    value directly and refine only a bracket around the best trend. It never
    assumes a hardware bound from a single sample.
    """
    rows = []
    for row in observations:
        if not isinstance(row, Mapping):
            continue
        x = _number(row.get(parameter))
        y = _number(row.get(metric))
        if x is not None and y is not None and x > 0:
            rows.append((x, y, row))
    rows.sort(key=lambda item: item[0])
    if len(rows) < 3:
        return {"status": "insufficient_evidence", "bound": False,
                "stop": False, "samples": len(rows), "reason": "need_three_trend_points"}
    improvements = []
    for previous, current in zip(rows, rows[1:]):
        improvements.append((current[1] - previous[1]) / max(abs(previous[1]), 1e-12))
    utilization = []
    for _, _, row in rows[-3:]:
        for key in ("compute_utilization", "ai_core_utilization", "aic_utilization",
                    "pipe_utilization", "utilization"):
            value = _number(row.get(key))
            if value is not None:
                utilization.append(value / 100.0 if value > 1.0 else value)
                break
    tail = improvements[-2:]
    plateau = len(tail) == 2 and all(abs(value) <= plateau_tolerance for value in tail)
    regression = len(tail) == 2 and all(value <= -regression_tolerance for value in tail)
    saturated = bool(utilization) and sum(utilization) / len(utilization) >= utilization_threshold and plateau
    bound = plateau or regression or saturated
    reason = ("compute_bound" if saturated else
              "throughput_plateau" if plateau else
              "throughput_regression" if regression else "trend_still_improving")
    return {"status": "bound" if bound else "expand", "bound": bound, "stop": bound,
            "samples": len(rows), "parameter": parameter, "metric": metric,
            "improvements": improvements, "utilization_tail": utilization,
            "reason": reason, "next_action": "refine_bracket" if bound else "probe_wider"}
