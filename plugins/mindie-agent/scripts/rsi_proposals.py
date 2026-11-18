#!/usr/bin/env python3
"""Generate bounded, deterministic RSI hypotheses from a cost-model gap.

[Role] Provides the proposer phase when a user supplies a candidate space only
       implicitly through ``auto_propose``.
[Invariants] Proposals are data, never shell commands; explicit candidates are
             left to the caller and the result is bounded.
[Timeouts & Locks] Pure computation; no source tree or NPU is touched.
[Side-Effect Policy] Returns JSON-compatible candidate descriptions only.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SCHEMA = "mindie-agent/rsi-proposals/v1"
_DIRECTIONS = {
    "draft": ("draft", {"method": "framework", "target": "draft_s"}),
    "verify": ("verify", {"method": "tree", "target": "verify_s"}),
    "scheduler": ("scheduler", {"method": "batching", "target": "scheduler_s"}),
    "memory": ("memory", {"method": "kernel", "target": "memory_s"}),
    "unknown": ("unknown", {"method": "framework", "target": "unknown"}),
}


def propose(payload: Mapping[str, Any], cost_report: Mapping[str, Any] | None = None,
            prior_memory: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Return hypotheses ordered by the measured largest gap.

    A configured ``proposal_directions`` list is honoured; otherwise the
    bottleneck produces a small framework/tree/kernel/batching shortlist. The
    output remains a candidate description for the existing action adapter.
    """
    report = cost_report or {}
    direction = str(report.get("largest_gap") or "unknown")
    configured = payload.get("proposal_directions")
    names = [str(item) for item in configured] if isinstance(configured, list) else []
    if not names:
        names = [direction] + [name for name in ("draft", "verify", "scheduler", "memory")
                               if name != direction]
    limit = max(1, min(int(payload.get("max_proposals", 8)), 32))
    candidates: list[dict[str, Any]] = []
    seen = set()
    for name in names:
        if name in seen or name not in _DIRECTIONS:
            continue
        seen.add(name)
        label, config = _DIRECTIONS[name]
        candidates.append({
            "method": config["method"],
            "config": {"direction": label, "target": config["target"]},
            "hypothesis": f"reduce_{label}_gap",
            "validation_plan": ["build", "accuracy", "performance", "sealed_final"],
            "memory_candidates": [item.get("candidate_id") for item in prior_memory
                                  if item.get("mechanism") == label],
            "status": "pending",
        })
        if len(candidates) >= limit:
            break
    return candidates
