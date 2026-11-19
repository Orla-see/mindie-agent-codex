import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "plugins" / "mindie-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rsi_cost_model import diagnose
from rsi_distill_queue import DistillQueue, to_knowledge_request
from rsi_profiler_ascend import parse
from rsi_token_forcing import score


def test_token_forcing_requires_exact_teacher_forced_match():
    result = score([
        {"position": 0, "candidate_token_id": 3, "stock_token_id": 3, "teacher_forced": True},
        {"position": 1, "candidate_token_id": 4, "stock_token_id": 5, "teacher_forced": True},
    ])
    assert result["passed"] is False
    assert result["mismatches"] == 1


def test_msprof_csv_is_normalized_and_feeds_roofline(tmp_path):
    path = tmp_path / "kernel_details.csv"
    path.write_text(
        "name,duration_us,flops,bytes\n"
        "draft_forward,10,100,1000\n"
        "verify_gemm,20,200,2000\n",
        encoding="utf-8",
    )
    profile = parse([path])
    assert profile["kernel_count"] == 2
    assert profile["stages"]["draft"] > 0
    report = diagnose(
        [{"elapsed_s": 1, "completion_tokens": 10, "baseline_elapsed_s": 2,
          "baseline_tokens": 10, "draft_s": .1, "verify_s": .5, "scheduler_s": .1}],
        peak_flops_per_s=1000,
        peak_bytes_per_s=1000,
        kernel_rows=profile["kernel_rows"],
    )
    assert report["roofline"]["measured_kernel_count"] == 2


def test_distill_queue_is_idempotent_and_converts_schema(tmp_path):
    request = {
        "schema": "mindie-agent/rsi-distill-request/v1",
        "inputs": [{"kind": "reproduced_empirical", "outcome": "verified",
                    "source_locator": "rsi://deployment/mechanism", "content": "{}",
                    "evidence_refs": []}],
        "task_state": "verified_milestone",
    }
    native = to_knowledge_request(request, domain="vllm-ascend")
    assert native["task_state"] == "verified_milestone"
    queue = DistillQueue(tmp_path / "queue.jsonl")
    first = queue.enqueue(request)
    second = queue.enqueue(request)
    assert first["queue_id"] == second["queue_id"]
    assert len(queue.pending()) == 1
