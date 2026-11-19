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

try:
    from rsi_trend import assess_bound
    from rsi_knowledge import knowledge_candidates, research_request
except ImportError:  # pragma: no cover
    from .rsi_trend import assess_bound
    from .rsi_knowledge import knowledge_candidates, research_request

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
    scope = payload.get("optimization_scope")
    rows = payload.get("profile_observations")
    if isinstance(scope, Mapping) and scope.get("strategy") == "trend_bound":
        parameter = str(next(iter((scope.get("parameters") or {"num_speculative_tokens": "adaptive"}))))
        origin = scope.get("current_value")
        if origin is None:
            origin = payload.get(f"current_{parameter}") or payload.get(parameter)
        trend = assess_bound(rows if isinstance(rows, list) else [], parameter,
                             origin=int(origin) if isinstance(origin, (int, float)) else None,
                             step=2)
        return [{
            "method": "adaptive-runtime-parameter",
            "config": {parameter: "probe_adjacent_by_two", "search": "trend_bound",
                       "next_values": trend.get("next_values", [])},
            "hypothesis": f"expand_{parameter}_until_bound",
            "trend": trend,
            "stop_when": "compute_bound_or_throughput_plateau_or_regression",
            "validation_plan": ["profile", "accuracy", "performance"],
            "status": "pending",
        }]
    scope_kind = scope.get("kind") if isinstance(scope, Mapping) else None
    request_text = " ".join(str(payload.get(key) or "") for key in (
        "objective", "optimization_request", "test_description")).lower()
    knowledge_mode = bool(
        payload.get("knowledge_search") is True
        or payload.get("auto_research") is True
        or scope_kind in {"algorithm", "knowledge", "algorithm_family"}
        or (isinstance(scope, Mapping) and scope.get("strategy") in {"knowledge_rank", "research_first"})
        or any(word in request_text for word in (
            "dflash", "dflash2", "mtp", "dspark", "ssm", "动态投机", "dynamic speculation",
            "算法", "algorithm", "投机推理方法")))
    if knowledge_mode:
        limit = max(1, min(int(payload.get("max_proposals", 6)), 6))
        cards = payload.get("knowledge_cards") or payload.get("foundation_cards")
        failed = {
            str(item.get("mechanism"))[len("knowledge:"):]
            for item in prior_memory
            if isinstance(item, Mapping)
            and str(item.get("mechanism") or "").startswith("knowledge:")
            and item.get("outcome") in {"failed", "infra_failed", "correctness_failed", "performance_failed"}
        }
        result = knowledge_candidates(payload, report, cards=cards, top_k=limit,
                                      excluded_ids=failed)
        request = research_request(payload)
        request["target_candidates"] = limit
        request["continuation_required"] = len(result) < limit
        request["continuation_mode"] = (
            "query_next_source_until_top6_or_exhausted"
            if request["algorithm_exploration_authorized"]
            else "query_repository_and_configured_sources_until_top6_or_exhausted"
        )
        for item in result:
            item["research_request"] = request
        return result
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
