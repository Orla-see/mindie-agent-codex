import sys
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "plugins" / "mindie-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rsi_compare import compare
from rsi_memory import distill_request
from rsi_workload import build_manifest, request_record


def test_manifest_captures_conditions_without_host_paths():
    manifest = build_manifest({
        "objective": "reduce TTFT p95",
        "test_command": ["vllm", "bench", "serve"],
        "batch_size": 1,
        "repeats": 3,
        "warmup_runs": 1,
    }, startup_text="#!/bin/sh\nserve", test_text="benchmark")
    assert manifest["schema"] == "mindie-agent/rsi-workload/v1"
    assert manifest["objective"]["metric"] == "ttft_p95"
    assert manifest["repeats"] == 3
    assert "/" not in manifest["startup_sha256"]
    assert manifest["startup_parameters"]["num_speculative_tokens"] == 5
    assert manifest["startup_parameters"]["num_speculative_tokens_source"] == "default"


def test_manifest_prefers_startup_speculative_length():
    manifest = build_manifest({}, startup_text="serve --num-speculative-tokens 9")
    assert manifest["startup_parameters"]["num_speculative_tokens"] == 9
    assert manifest["startup_parameters"]["num_speculative_tokens_source"] == "startup_script"


def test_manifest_defaults_to_one_warmup_and_three_measurements():
    manifest = build_manifest({})
    assert manifest["warmup_runs"] == 1
    assert manifest["repeats"] == 3


def test_objective_comparison_uses_latency_direction():
    result = compare({
        "paired_runs": [
            {"baseline_latency_s": 2.0, "candidate_latency_s": 1.0},
            {"baseline_latency_s": 2.2, "candidate_latency_s": 1.1},
        ],
    }, "reduce end to end latency", {"min_speedup": 1.0})
    assert result["metric"] == "latency_p50"
    assert result["ci_lower_ratio"] > 1.0


def test_throughput_comparison_uses_paired_output_rates():
    result = compare({
        "paired_runs": [
            {"baseline_output_tok_s": 100.0, "candidate_output_tok_s": 110.0},
            {"baseline_output_tok_s": 100.0, "candidate_output_tok_s": 112.0},
        ],
    }, "increase decode throughput", {"min_speedup": 1.0})
    assert result["metric"] == "throughput"
    assert result["geometric_ratio"] > 1.0


def test_identity_and_task_accuracy_are_separate():
    row = request_record({
        "id": "req-1",
        "input_ids": [1, 2],
        "output_ids": [3, 4],
        "latency_s": 1.0,
        "completion_tokens": 2,
        "accuracy_score": 1.0,
    }, run_index=0)
    assert row["output_ids_sha256"]
    assert row["task_accuracy"] == 1.0
    result = compare({
        "paired_runs": [{
            "baseline_output_ids": [3, 4],
            "candidate_output_ids": [3, 5],
            "baseline_output_tok_s": 100,
            "candidate_output_tok_s": 110,
        }],
        "task_accuracy": {"baseline": 1.0, "candidate": 1.0},
    }, "increase throughput", {"min_speedup": 1.0})
    assert result["output_identity"]["mismatch_pairs"] == 1
    assert result["task_accuracy"]["candidate"] == 1.0


def test_distill_records_scenario_without_speculative_length():
    request = distill_request({
        "mechanism": "adaptive-runtime-parameter",
        "mechanism_family": "speculative-decoding",
        "outcome": "verified",
        "applicability": {
            "feature": "adaptive-runtime-parameter",
            "bottleneck": "memory_bound",
            "workload": {"batch_size": 1},
            "num_speculative_tokens": 7,
        },
        "performance_evidence": {"num_speculative_tokens": 7, "ci_lower": 1.08},
    }, deployment_signature="deployment")
    content = request["inputs"][0]["content"]
    assert "num_speculative_tokens" not in content
    assert "memory_bound" in content
    assert "applicable_when" in content
