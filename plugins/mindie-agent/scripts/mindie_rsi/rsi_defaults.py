#!/usr/bin/env python3
"""Defaults and conversational questions for natural-language RSI plans.

The defaults describe policy and tool selection. They do not invent a customer
build or correctness command; those remain explicit runtime capabilities.
"""
from __future__ import annotations

from typing import Any, Mapping

try:
    from rsi_knowledge import refresh_policy
except ImportError:  # pragma: no cover - direct package execution fallback
    from .rsi_knowledge import refresh_policy

SPECULATIVE_METHODS = ("mtp", "dflash", "dflash2", "dspark")
DEFAULT_CANDIDATE_ORDER = ("runtime_parameters", "startup_features")
ALGORITHM_EXPLORATION_POLICIES = ("disabled", "user_authorized")
DEFAULT_PROFILE_BINDING = {
    "tool": "msprof",
    "command": ["msprof"],
    "aic_metrics": ["PipeUtilization"],
    "source": "container_path_or_PATH",
}
DEFAULT_OPTIMIZATION_SCOPE = {
    "kind": "runtime_parameters",
    "parameters": {"num_speculative_tokens": "adaptive"},
    "strategy": "trend_bound",
    "seed": "from_startup_script",
    "growth": "bidirectional_step_two",
    "reason": "No optimization scope was specified; probe K-2 and K+2, stop a direction at its first loss, and test K=0 only after sustained downward improvement.",
}


def default_settings(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return explicit, auditable defaults without overwriting user inputs."""
    value = dict(payload)
    profile = dict(DEFAULT_PROFILE_BINDING)
    supplied = value.get("profile_binding")
    if isinstance(supplied, Mapping):
        profile.update(dict(supplied))
    scope = value.get("optimization_scope")
    if not isinstance(scope, Mapping):
        scope = DEFAULT_OPTIMIZATION_SCOPE
    authorized = algorithm_exploration_allowed(value)
    candidate_order = value.get("candidate_order")
    if not isinstance(candidate_order, list):
        candidate_order = list(DEFAULT_CANDIDATE_ORDER)
        if authorized:
            candidate_order.append("source_changes")
    policy = str(value.get("algorithm_exploration_policy") or
                 ("user_authorized" if authorized else "disabled"))
    sources = ["repository", "configured_feed"]
    if authorized:
        sources.insert(1, "academic_paper")
    return {
        "profile_binding": profile,
        "performance_policy": str(value.get("performance_policy") or "default"),
        "accuracy_policy": str(value.get("accuracy_policy") or "default"),
        "correctness_mode": str(value.get("correctness_mode") or "task_accuracy"),
        "optimization_scope": dict(scope),
        "speculative_methods": list(SPECULATIVE_METHODS),
        "runtime_parameter_policy": str(value.get("runtime_parameter_policy") or "discover_from_startup"),
        "startup_feature_policy": str(value.get("startup_feature_policy") or "allow_existing_features"),
        "source_change_policy": str(value.get("source_change_policy") or "parameters_only"),
        "source_scope_policy": str(value.get("source_scope_policy") or "vllm_ascend_only"),
        "algorithm_exploration_policy": policy,
        "algorithm_exploration_authorized": authorized,
        "candidate_order": candidate_order,
        "implementation_provider": str(value.get("implementation_provider") or "auto"),
        "auto_distill": bool(value.get("auto_distill", True)),
        "knowledge_timeout_s": int(value.get("knowledge_timeout_s") or 600),
        "knowledge": {
            "candidate_mode": ("top6_with_algorithm_exploration" if authorized
                               else "top6_existing_capabilities"),
            "refresh": refresh_policy(value),
            "sources": sources,
            "distill_policy": "verified_sources_only",
        },
    }


def algorithm_exploration_allowed(payload: Mapping[str, Any]) -> bool:
    """Return whether the user explicitly enabled algorithms absent from the repo."""
    if payload.get("allow_unimplemented_algorithms") is True:
        return True
    if payload.get("allow_algorithm_exploration") is True:
        return True
    policy = str(payload.get("algorithm_exploration_policy") or "").strip().lower()
    if policy in ALGORITHM_EXPLORATION_POLICIES:
        return policy == "user_authorized"
    if policy:
        return False
    # Backward-compatible explicit authorization used by earlier plans.
    return payload.get("source_change_policy") == "allow_source_changes"


def questions(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ask only for values that have no safe default."""
    result: list[dict[str, Any]] = []
    text = " ".join(str(payload.get(key) or "") for key in ("objective", "test_description", "optimization_request")).lower()
    method = str(payload.get("speculative_method") or "").lower()
    mentions_speculation = method in SPECULATIVE_METHODS or any(name in text for name in SPECULATIVE_METHODS + ("speculative", "投机"))
    if mentions_speculation and not method:
        result.append({
            "field": "speculative_method",
            "question": "Which speculative method should be optimized?",
            "choices": list(SPECULATIVE_METHODS),
        })
    if mentions_speculation and not (payload.get("speculative_weights") or payload.get("draft_model") or payload.get("speculative_model")):
        result.append({
            "field": "speculative_weights",
            "question": "Provide the draft or MTP weight path. It may be read from the startup model or speculative-config arguments.",
            "choices": ["provide_path"],
        })
    return result
