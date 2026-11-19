#!/usr/bin/env python3
"""Turn verified knowledge cards into ranked RSI implementation hypotheses.

The knowledge service owns collection and revisioning.  This module is the
small, deterministic bridge used by RSI after ``knowledge_query`` (or a local
card file) has returned cards.  It never treats a paper's reported speedup as
a customer result and it never emits shell commands.  A candidate contains an
implementation request; only the runtime adapter and its isolated worktree may
turn that request into source changes.
"""
from __future__ import annotations

import re
import time
from typing import Any, Mapping, Sequence

SCHEMA = "mindie-agent/rsi-knowledge/v1"
DEFAULT_REFRESH_SECONDS = 86400
TOP_K = 6
DEFAULT_CANDIDATE_ORDER = ("runtime_parameters", "startup_features")
AUTHORIZED_CANDIDATE_ORDER = (*DEFAULT_CANDIDATE_ORDER, "source_changes")


# These are navigation cards, rather than claims about the customer's model.
# The ranges are priors used to order experiments and are deliberately marked
# as unverified until the local profile/correctness/performance gates pass.
DEFAULT_CARDS: tuple[dict[str, Any], ...] = (
    {
        "id": "speculative-length-trend",
        "title": "Adaptive speculative length",
        "family": "speculative_decoding",
        "signals": {"always": True},
        "knobs": ["num_speculative_tokens"],
        "requirements": ["runtime_accepts_speculative_length"],
        "expected_gain_pct": {"low": 0.0, "high": 8.0},
        "confidence": "prior",
        "implementation": {"kind": "runtime_config", "patch_required": False},
        "source_refs": ["builtin://rsi/speculative-length-trend"],
    },
    {
        "id": "speculative-dflash-draft",
        "title": "Parallel diffusion drafter (DFlash/DFlash2)",
        "family": "speculative_decoding",
        "signals": {"verify_dominates": True, "acceptance_available": True},
        "knobs": ["speculative_method", "speculative_model", "num_speculative_tokens"],
        "requirements": ["draft_weights", "runtime_supports_dflash"],
        "expected_gain_pct": {"low": 3.0, "high": 20.0},
        "confidence": "paper_prior",
        "implementation": {"kind": "runtime_config", "patch_required": False},
        "source_refs": ["arxiv:2602.06036"],
    },
    {
        "id": "speculative-mtp-heads",
        "title": "Multi-token prediction heads (MTP/Medusa-like)",
        "family": "speculative_decoding",
        "signals": {"acceptance_available": True, "draft_missing": True},
        "knobs": ["speculative_method", "mtp_weights", "num_speculative_tokens"],
        "requirements": ["mtp_weights", "runtime_supports_mtp"],
        "expected_gain_pct": {"low": 2.0, "high": 15.0},
        "confidence": "paper_prior",
        "implementation": {"kind": "runtime_config", "patch_required": False},
        "source_refs": ["arxiv:2401.10774"],
    },
    {
        "id": "speculative-dynamic-acceptance",
        "title": "Dynamic speculation and acceptance-aware scheduling",
        "family": "speculative_decoding",
        "signals": {"acceptance_variance": True, "scheduler_dominates": True},
        "knobs": ["dynamic_speculation", "acceptance_threshold", "max_num_seqs"],
        "requirements": ["runtime_supports_dynamic_speculation"],
        "expected_gain_pct": {"low": 1.0, "high": 12.0},
        "confidence": "hypothesis",
        "implementation": {"kind": "runtime_or_source", "patch_required": True},
        "source_refs": ["builtin://rsi/acceptance-aware-scheduling"],
    },
    {
        "id": "speculative-tree-shape",
        "title": "Acceptance-aware tree width/depth",
        "family": "speculative_decoding",
        "signals": {"acceptance_available": True, "verify_dominates": True},
        "knobs": ["draft_tree_width", "draft_tree_depth", "num_speculative_tokens"],
        "requirements": ["runtime_supports_tree_speculation"],
        "expected_gain_pct": {"low": 1.0, "high": 10.0},
        "confidence": "prior",
        "implementation": {"kind": "runtime_config", "patch_required": False},
        "source_refs": ["builtin://rsi/acceptance-aware-tree"],
    },
    {
        "id": "speculative-ssm-drafter",
        "title": "SSM or recurrent drafter",
        "family": "speculative_decoding",
        "signals": {"draft_dominates": True, "memory_bound": True},
        "knobs": ["speculative_method", "draft_weights", "draft_device"],
        "requirements": ["ssm_draft_weights", "runtime_supports_ssm_draft"],
        "expected_gain_pct": {"low": 2.0, "high": 18.0},
        "confidence": "hypothesis",
        "implementation": {"kind": "runtime_or_source", "patch_required": True},
        "source_refs": ["builtin://rsi/ssm-drafter"],
    },
    {
        "id": "speculative-eagle-drafter",
        "title": "Hidden-state trained drafter (EAGLE-like)",
        "family": "speculative_decoding",
        "signals": {"acceptance_available": True, "draft_dominates": True},
        "knobs": ["speculative_method", "draft_weights", "num_speculative_tokens"],
        "requirements": ["draft_weights", "runtime_supports_eagle"],
        "expected_gain_pct": {"low": 2.0, "high": 16.0},
        "confidence": "paper_prior",
        "implementation": {"kind": "runtime_or_source", "patch_required": True},
        "source_refs": ["builtin://rsi/eagle-drafter"],
    },
)


def _safe_card(value: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep only JSON-like, bounded card data from a knowledge provider."""
    card_id = str(value.get("id") or value.get("unit_id") or "").strip()
    if not card_id or not re.fullmatch(r"[A-Za-z0-9_.:/@-]{1,160}", card_id):
        return None
    result = dict(value)
    result["id"] = card_id
    for key in ("title", "family", "confidence"):
        if key in result:
            result[key] = str(result[key])[:300]
    refs = result.get("source_refs") or result.get("refs") or result.get("evidence_refs")
    if isinstance(refs, (list, tuple)):
        result["source_refs"] = [str(item)[:500] for item in refs[:16]]
    elif refs:
        result["source_refs"] = [str(refs)[:500]]
    else:
        result["source_refs"] = []
    gain = result.get("expected_gain_pct")
    if isinstance(gain, Mapping):
        try:
            low, high = float(gain.get("low", 0)), float(gain.get("high", 0))
        except (TypeError, ValueError):
            low, high = 0.0, 0.0
        result["expected_gain_pct"] = {"low": max(-100.0, min(low, high)),
                                        "high": max(-100.0, min(1000.0, max(low, high)))}
    else:
        result["expected_gain_pct"] = {"low": 0.0, "high": 0.0}
    return result


def extract_cards(value: Any) -> list[dict[str, Any]]:
    """Accept knowledge-query, explain, and local-card-file response shapes."""
    rows: Any = value
    if isinstance(value, Mapping):
        for key in ("knowledge_cards", "foundation_cards", "cards", "units", "results"):
            if isinstance(value.get(key), list):
                rows = value[key]
                break
    if not isinstance(rows, list):
        rows = []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        card = _safe_card(item)
        if card is not None and card["id"] not in seen:
            seen.add(card["id"])
            result.append(card)
    return result


def _text(payload: Mapping[str, Any]) -> str:
    return " ".join(str(payload.get(key) or "") for key in (
        "objective", "optimization_request", "test_description", "goal")).lower()


def _context(payload: Mapping[str, Any], cost_report: Mapping[str, Any] | None) -> dict[str, Any]:
    report = cost_report or {}
    observations = payload.get("profile_observations")
    observations = observations if isinstance(observations, list) else []
    rows = [row for row in observations if isinstance(row, Mapping)]
    acceptance = [float(row[key]) for row in rows for key in ("acceptance_rate", "accepted_rate")
                  if isinstance(row.get(key), (int, float))]
    verify = float(report.get("stage_fractions", {}).get("verify", 0) or 0) \
        if isinstance(report.get("stage_fractions"), Mapping) else 0.0
    draft = float(report.get("stage_fractions", {}).get("draft", 0) or 0) \
        if isinstance(report.get("stage_fractions"), Mapping) else 0.0
    return {
        "text": _text(payload),
        "largest_gap": str(report.get("largest_gap") or ""),
        "acceptance_rate": (sum(acceptance) / len(acceptance)) if acceptance else None,
        "acceptance_variance": len(acceptance) >= 2 and (max(acceptance) - min(acceptance) >= 0.15),
        "verify_fraction": verify,
        "draft_fraction": draft,
        "capabilities": payload.get("runtime_capabilities") if isinstance(payload.get("runtime_capabilities"), Mapping) else {},
    }


def _algorithm_exploration_allowed(payload: Mapping[str, Any]) -> bool:
    """Gate methods that are absent from the checked-out repository."""
    if payload.get("allow_unimplemented_algorithms") is True:
        return True
    if payload.get("allow_algorithm_exploration") is True:
        return True
    policy = str(payload.get("algorithm_exploration_policy") or "").strip().lower()
    if policy == "user_authorized":
        return True
    if policy == "disabled":
        return False
    if policy:
        return False
    defaults = payload.get("defaults")
    if isinstance(defaults, Mapping) and defaults.get("algorithm_exploration_authorized") is True:
        return True
    # Preserve the earlier explicit source-change opt-in as an authorization.
    return payload.get("source_change_policy") == "allow_source_changes"


def _matches(card: Mapping[str, Any], context: Mapping[str, Any]) -> tuple[int, list[str]]:
    signals = card.get("signals") if isinstance(card.get("signals"), Mapping) else {}
    text, gap = str(context.get("text", "")), str(context.get("largest_gap", ""))
    score, reasons = 0, []
    if signals.get("always"):
        score += 8
        reasons.append("general speculative-decoding baseline")
    if signals.get("verify_dominates") and (gap == "verify" or float(context.get("verify_fraction") or 0) >= 0.45):
        score += 28
        reasons.append("verification is a large measured cost")
    if signals.get("draft_dominates") and (gap == "draft" or float(context.get("draft_fraction") or 0) >= 0.35):
        score += 28
        reasons.append("drafting is a large measured cost")
    if signals.get("scheduler_dominates") and gap == "scheduler":
        score += 26
        reasons.append("scheduler is the measured bottleneck")
    if signals.get("memory_bound") and gap == "memory":
        score += 20
        reasons.append("memory is the measured bottleneck")
    if signals.get("acceptance_available") and context.get("acceptance_rate") is not None:
        score += 15
        reasons.append("acceptance data is available")
    if signals.get("acceptance_variance") and context.get("acceptance_variance"):
        score += 24
        reasons.append("acceptance varies across the workload")
    if any(word in text for word in ("dflash", "dflash2")) and "dflash" in str(card.get("id")):
        score += 35
        reasons.append("user requested DFlash")
    if "mtp" in text and "mtp" in str(card.get("id")):
        score += 35
        reasons.append("user requested MTP")
    if any(word in text for word in ("ssm", "recurrent")) and "ssm" in str(card.get("id")):
        score += 35
        reasons.append("user requested an SSM/recurrent drafter")
    if any(word in text for word in ("dynamic", "动态", "acceptance", "接受率")) and "dynamic" in str(card.get("id")):
        score += 30
        reasons.append("user requested dynamic acceptance/scheduling")
    return score, reasons


def _phase(card: Mapping[str, Any]) -> str:
    implementation = card.get("implementation")
    if not isinstance(implementation, Mapping):
        # Older repository cards omitted implementation metadata.  Keep those
        # cards usable as existing runtime candidates.
        return "runtime_parameters"
    kind = implementation.get("kind") or ""
    if implementation.get("patch_required") is True or kind in {
        "runtime_or_source", "source", "source_code"
    }:
        return "source_changes"
    if kind == "runtime_config":
        knobs = card.get("knobs") if isinstance(card.get("knobs"), list) else []
        return "startup_features" if any(str(item) in {
            "dynamic_speculation", "acceptance_threshold", "draft_tree_width", "draft_tree_depth"
        } for item in knobs) else "runtime_parameters"
    return "runtime_parameters"


def rank_cards(cards: Sequence[Mapping[str, Any]], payload: Mapping[str, Any],
               cost_report: Mapping[str, Any] | None = None, *, top_k: int = TOP_K,
               excluded_ids: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Rank cards by applicability, gain prior, evidence and runtime fit."""
    context = _context(payload, cost_report)
    explore_unimplemented = _algorithm_exploration_allowed(payload)
    scope = payload.get("optimization_scope")
    algorithm_mode = isinstance(scope, Mapping) and scope.get("kind") in {
        "algorithm", "knowledge", "algorithm_family"
    }
    length_requested = any(word in context["text"] for word in (
        "num_speculative_tokens", "speculative length", "投机长度", "投机 token 长度"))
    candidates: list[dict[str, Any]] = []
    excluded = {str(value) for value in excluded_ids}
    for raw in cards:
        card = _safe_card(raw)
        if card is None:
            continue
        if card["id"] in excluded:
            continue
        if algorithm_mode and card["id"] == "speculative-length-trend" and not length_requested:
            continue
        match, reasons = _matches(card, context)
        gain = card["expected_gain_pct"]
        midpoint = (gain["low"] + gain["high"]) / 2.0
        requirements = card.get("requirements") if isinstance(card.get("requirements"), list) else []
        capabilities = context["capabilities"]
        unknown = [str(req) for req in requirements if capabilities.get(req) is not True]
        confidence = 1.0 if not unknown else 0.65
        score = round(match + min(25.0, max(0.0, midpoint)) * confidence, 3)
        phase = _phase(card)
        if phase == "source_changes" and not explore_unimplemented:
            continue
        candidates.append({"card": card, "score": score, "match_reasons": reasons,
                           "unknown_requirements": unknown,
                           "phase": phase,
                           "theoretical_precheck": {
                               "expected_gain_positive": gain["high"] > 0,
                               "bottleneck_match_score": match,
                               "implementation_cost": "low" if phase == "runtime_parameters" else (
                                   "medium" if phase == "startup_features" else "high"),
                               "eligible_for_measurement": gain["high"] > 0 and not unknown,
                           },
                           "estimated_gain_pct": {"low": gain["low"], "high": gain["high"],
                                                  "confidence": confidence}})
    order = payload.get("candidate_order")
    if not isinstance(order, list):
        defaults = payload.get("defaults")
        order = defaults.get("candidate_order") if isinstance(defaults, Mapping) else None
    order = tuple(str(item) for item in order) if isinstance(order, list) else (
        AUTHORIZED_CANDIDATE_ORDER if explore_unimplemented else DEFAULT_CANDIDATE_ORDER)
    if not explore_unimplemented:
        order = tuple(item for item in order if item != "source_changes") or DEFAULT_CANDIDATE_ORDER
    phase_rank = {name: index for index, name in enumerate(order)}
    candidates.sort(key=lambda item: (phase_rank.get(item["phase"], len(order)),
                                      -item["score"], str(item["card"]["id"])))
    return candidates[:max(1, min(int(top_k), TOP_K))]


def candidate_from_ranked(item: Mapping[str, Any]) -> dict[str, Any]:
    card = item["card"]
    implementation = card.get("implementation") if isinstance(card.get("implementation"), Mapping) else {}
    knobs = card.get("knobs") if isinstance(card.get("knobs"), list) else []
    mapping = card.get("repository_mapping") if isinstance(card.get("repository_mapping"), Mapping) else {}
    algorithm = card.get("algorithm") if isinstance(card.get("algorithm"), Mapping) else {}
    tests = card.get("tests") if isinstance(card.get("tests"), Mapping) else {}
    constraints = card.get("implementation_constraints") if isinstance(card.get("implementation_constraints"), Mapping) else {}
    implementation_card = {
        "schema": "mindie-agent/implementation-card/v1",
        "card_id": card["id"],
        "family": card.get("family", "unknown"),
        "source_refs": card.get("source_refs", []),
        "repository_mapping": {
            "repository": mapping.get("repository", "vllm-ascend"),
            "revision": mapping.get("revision", "from_runtime_checkout"),
            "search_queries": mapping.get("search_queries") or [
                "speculative decoding", "draft worker", "candidate verification",
                "acceptance rate", "KV cache", "scheduler decode"
            ],
            "preferred_directories": mapping.get("preferred_directories") or [
                "vllm_ascend", "tests", "vllm/spec_decode"
            ],
            "entrypoints_to_find": mapping.get("entrypoints_to_find") or [
                "speculative config parser", "draft generation", "candidate verification",
                "scheduler decode path"
            ],
            "preferred_files": mapping.get("preferred_files") or [],
            "scope_policy": mapping.get("scope_policy") or "vllm_ascend_only",
        },
        "algorithm": {
            "steps": algorithm.get("steps") or [
                f"apply {card.get('title', card['id'])} to the measured bottleneck",
                "preserve target-model output correctness",
                "emit an auditable runtime or source change",
            ],
            "invariants": algorithm.get("invariants") or [
                "baseline behavior remains available for rollback",
                "correctness gates remain enabled",
            ],
            "tensor_contract": algorithm.get("tensor_contract") or {
                "shape": "discover_from_current_callsite",
                "dtype": "preserve_current_runtime_dtype",
                "device": "preserve_current_device_and_parallel_topology",
            },
        },
        "implementation_constraints": {
            "allowed_files": constraints.get("allowed_files") or ["vllm_ascend/**", "tests/**"],
            "forbidden_changes": constraints.get("forbidden_changes") or [
                "vllm/** unless explicitly authorized", "target model weights",
                "tokenizer semantics", "correctness gates"
            ],
            "integration": constraints.get("integration") or [
                "preserve existing API and startup compatibility",
                "use the current runtime abstractions before adding new ones",
            ],
        },
        "tests": {
            "unit": tests.get("unit") or ["configuration and candidate-shape paths"],
            "correctness": tests.get("correctness") or [
                "teacher forcing", "unseen prompts", "target output equivalence"
            ],
            "performance": tests.get("performance") or [
                "paired baseline and candidate benchmark", "msprof PipeUtilization"
            ],
        },
        "patch_contract": {
            "format": "unified_diff",
            "must_include": ["summary", "changed_files", "validation_plan"],
            "must_not_include": ["shell commands", "unbounded file rewrites"],
            "minimal_change": True,
            "style": "follow_existing_repository_style",
        },
    }
    return {
        "method": f"knowledge:{card['id']}",
        "config": {"knowledge_card": card["id"], "knobs": [str(knob) for knob in knobs]},
        "hypothesis": f"apply_{card['id']}_to_reduce_speculative_decoding_cost",
        "applicability": {"score": item["score"], "reasons": item["match_reasons"],
                           "unknown_requirements": item["unknown_requirements"]},
        "estimated_gain_pct": item["estimated_gain_pct"],
        "theoretical_precheck": item["theoretical_precheck"],
        "implementation_phase": item["phase"],
        "knowledge": {"schema": SCHEMA, "card_id": card["id"],
                       "family": card.get("family", "unknown"),
                       "source_refs": card.get("source_refs", []),
                       "confidence": card.get("confidence", "unknown")},
        "implementation": {"request": "generate_or_apply_in_isolated_worktree",
                           "kind": implementation.get("kind", "runtime_or_source"),
                           "patch_required": bool(implementation.get("patch_required", True)),
                           "knobs": [str(knob) for knob in knobs]},
        "implementation_card": implementation_card,
        "mechanism_family": card.get("family", "unknown"),
        "validation_plan": ["implement", "build", "profile", "accuracy", "performance", "sealed_final"],
        "status": "pending",
    }


def knowledge_candidates(payload: Mapping[str, Any], cost_report: Mapping[str, Any] | None = None,
                         *, cards: Any = None, top_k: int = TOP_K,
                         excluded_ids: Sequence[str] = ()) -> list[dict[str, Any]]:
    supplied = extract_cards(cards)
    pool = supplied or [dict(card) for card in DEFAULT_CARDS]
    return [candidate_from_ranked(item) for item in rank_cards(
        pool, payload, cost_report, top_k=top_k, excluded_ids=excluded_ids)]


def research_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Describe the bounded retrieval/distillation request for the knowledge service."""
    authorized = _algorithm_exploration_allowed(payload)
    source_types = ["repository", "configured_feed"]
    if authorized:
        source_types.insert(1, "academic_paper")
    return {
        "schema": "mindie-agent/rsi-research-request/v1",
        "query": _text(payload) or "speculative decoding serving performance",
        "source_types": source_types,
        "algorithm_exploration_authorized": authorized,
        "return_fields": ["applicable_when", "problem_solved", "knobs", "requirements",
                          "expected_gain_pct", "implementation", "source_refs"],
        "must_verify": ["source_locator", "runtime_compatibility", "customer_measurement"],
    }


def refresh_policy(payload: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any]:
    value = payload.get("knowledge_refresh")
    value = dict(value) if isinstance(value, Mapping) else {}
    interval = value.get("interval_seconds", DEFAULT_REFRESH_SECONDS)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        interval = DEFAULT_REFRESH_SECONDS
    interval = max(300, min(int(interval), 31_536_000))
    current = time.time() if now is None else float(now)
    last = value.get("last_sync_unix", 0)
    if isinstance(last, bool) or not isinstance(last, (int, float)):
        last = 0
    configured_sources = value.get("source_types")
    if isinstance(configured_sources, list):
        source_types = [str(item) for item in configured_sources]
    else:
        source_types = ["repository", "configured_feed"]
        if _algorithm_exploration_allowed(payload):
            source_types.insert(1, "academic_paper")
    return {"enabled": value.get("enabled", True) is not False,
            "interval_seconds": interval, "last_sync_unix": float(last),
            "refresh_due": current >= float(last) + interval,
            "source_types": source_types,
            "auto_distill": "verified_sources_only"}
