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
    "growth": "doubling_with_bracket_refinement",
    "reason": "No optimization scope was specified; adaptively tune speculative token count until the measured trend reaches a bound.",
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
    return {
        "profile_binding": profile,
        "performance_policy": str(value.get("performance_policy") or "ask_then_default"),
        "accuracy_policy": str(value.get("accuracy_policy") or "ask_then_default"),
        "correctness_mode": str(value.get("correctness_mode") or "task_accuracy"),
        "optimization_scope": dict(scope),
        "speculative_methods": list(SPECULATIVE_METHODS),
        "runtime_parameter_policy": str(value.get("runtime_parameter_policy") or "ask_then_discover"),
        "startup_feature_policy": str(value.get("startup_feature_policy") or "ask"),
        "source_change_policy": str(value.get("source_change_policy") or "ask"),
        "source_scope_policy": str(value.get("source_scope_policy") or "vllm_ascend_only"),
        "candidate_order": list(value.get("candidate_order") or [
            "runtime_parameters", "startup_features", "source_changes"]),
        "implementation_provider": str(value.get("implementation_provider") or "codex_fallback"),
        "auto_distill": bool(value.get("auto_distill", True)),
        "knowledge_timeout_s": int(value.get("knowledge_timeout_s") or 600),
        "knowledge": {
            "candidate_mode": "top6_when_algorithm_scope_or_algorithm_intent",
            "refresh": refresh_policy(value),
            "sources": ["repository", "academic_paper", "configured_feed"],
            "distill_policy": "verified_sources_only",
        },
    }


def questions(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build deterministic questions for fields the language layer must ask."""
    result: list[dict[str, Any]] = []
    if not payload.get("performance_policy"):
        result.append({
            "field": "performance_policy",
            "question": "是否指定自定义 performance 命令？不指定则使用 test_command/默认 benchmark。",
            "choices": ["default", "custom"],
            "default": "default",
        })
    if not payload.get("accuracy_policy"):
        result.append({
            "field": "accuracy_policy",
            "question": "是否指定自定义 accuracy 命令？不指定则使用默认正确性评测；没有可用默认评测时会暂停询问。",
            "choices": ["default", "custom"],
            "default": "default",
        })
    if not payload.get("correctness_mode"):
        result.append({
            "field": "correctness_mode",
            "question": "正确性使用任务答案精度，还是要求 candidate 与 stock 逐 token 无损一致？",
            "choices": ["task_accuracy", "lossless_token_forcing"],
            "default": "task_accuracy",
        })
    if not payload.get("runtime_parameter_policy"):
        result.append({
            "field": "runtime_parameter_policy",
            "question": "允许优化哪些启动参数？请提供参数名或选择自动识别启动脚本中的可调参数。",
            "choices": ["discover_from_startup", "user_list"],
            "default": "discover_from_startup",
        })
    if not payload.get("source_change_policy"):
        result.append({
            "field": "source_change_policy",
            "question": "是否允许修改源码实现算法？不允许时只调整启动参数。",
            "choices": ["parameters_only", "allow_source_changes"],
            "default": "parameters_only",
        })
    if not payload.get("source_scope_policy"):
        result.append({
            "field": "source_scope_policy",
            "question": "源码修改范围是否限制为 vllm-ascend？默认只改 vllm-ascend 和必要测试。",
            "choices": ["vllm_ascend_only", "allow_vllm_core_if_required"],
            "default": "vllm_ascend_only",
        })
    if not payload.get("startup_feature_policy"):
        result.append({
            "field": "startup_feature_policy",
            "question": "是否允许开启仓库已有但当前启动脚本未开启的特性？",
            "choices": ["parameters_only", "allow_existing_features"],
            "default": "parameters_only",
        })
    if not payload.get("candidate_order"):
        result.append({
            "field": "candidate_order",
            "question": "候选顺序是否采用默认的启动参数→已有特性→源码实现？",
            "choices": ["default_order", "user_order"],
            "default": "default_order",
        })
    scope = payload.get("optimization_scope")
    if not isinstance(scope, Mapping) and not payload.get("candidate_space") and not payload.get("candidates"):
        result.append({
            "field": "optimization_scope",
            "question": "优化范围是什么？未指定时默认优化 num_speculative_tokens。",
            "choices": ["num_speculative_tokens", "runtime_flags", "algorithm"],
            "default": "num_speculative_tokens",
        })
    text = " ".join(str(payload.get(key) or "") for key in ("objective", "test_description", "optimization_request")).lower()
    method = str(payload.get("speculative_method") or "").lower()
    mentions_speculation = method in SPECULATIVE_METHODS or any(name in text for name in SPECULATIVE_METHODS + ("speculative", "投机"))
    if mentions_speculation and not method:
        result.append({
            "field": "speculative_method",
            "question": "要优化哪种投机方法？",
            "choices": list(SPECULATIVE_METHODS),
        })
    if mentions_speculation and not (payload.get("speculative_weights") or payload.get("draft_model") or payload.get("speculative_model")):
        result.append({
            "field": "speculative_weights",
            "question": "请提供投机方法对应的 draft/MTP 权重路径；可从启动脚本的 model/speculative-config 中查找。",
            "choices": ["provide_path"],
        })
    return result
