import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mindie-agent/scripts"
sys.path.insert(0, str(SCRIPTS))

import rsi_cost_model  # noqa: E402
import rsi_evidence  # noqa: E402
import rsi_memory  # noqa: E402
import rsi_proposals  # noqa: E402
import rsi_sealed_final  # noqa: E402
import rsi_workspace  # noqa: E402


def test_cost_model_marks_missing_profile_evidence_and_bottleneck():
    report = rsi_cost_model.diagnose([{
        "elapsed_ms": 1000, "completion_tokens": 100,
        "baseline_elapsed_ms": 1200, "baseline_tokens": 100,
        "draft_ms": 100, "verify_ms": 700, "scheduler_ms": 100,
    }])
    assert report["samples"] == 1
    assert report["largest_gap"] == "verify"
    assert report["confidence"] > 0
    assert "stage_times_missing" not in report["evidence"]


def test_memory_store_is_replayable(tmp_path):
    store = rsi_memory.MemoryStore(tmp_path / "memory.jsonl")
    store.append({"deployment_signature": "host-a", "candidate_id": "c1",
                  "mechanism": "tree", "correctness_contract": {"exact": True}})
    assert store.find("host-a", "tree")[0]["candidate_id"] == "c1"
    assert json.loads((tmp_path / "memory.jsonl").read_text())["schema"] == rsi_memory.SCHEMA


def test_proposer_prioritizes_cost_model_gap():
    candidates = rsi_proposals.propose({}, {"largest_gap": "scheduler"})
    assert candidates[0]["method"] == "batching"
    assert candidates[0]["config"]["direction"] == "scheduler"


def test_sealed_final_requires_all_independent_evidence():
    common = {
        "unseen_prompts": [{"id": "u1"}, {"id": "u2"}, {"id": "u3"}],
        "long_outputs": [{"id": "l1", "output_tokens": 1024}],
        "paired_speedups": [1.2, 1.3, 1.25, 1.22, 1.28],
        "token_forcing_rows": [
            {"position": 0, "teacher_forced": True, "token_match": True},
            {"position": 1, "teacher_forced": True, "token_match": True},
        ],
    }
    report = rsi_sealed_final.evaluate(common)
    assert report["passed"] is True
    assert report["gates"]["holdout_disjoint"] is True
    broken = dict(common, token_forcing_rows=[{"teacher_forced": True, "token_match": False}])
    assert rsi_sealed_final.evaluate(broken)["passed"] is False


def test_evidence_contract_rejects_short_or_incomplete_runtime_receipts():
    paired = rsi_evidence.validate_paired({"paired_speedups": [1.2, 1.1]}, min_runs=5)
    assert paired["passed"] is False and paired["runs"] == 2
    profile = rsi_evidence.validate_profile({"profile_observations": [{
        "elapsed_ms": 100, "completion_tokens": 10,
    }]}, min_samples=5)
    assert profile["passed"] is False
    teacher = rsi_evidence.validate_teacher_forcing({"token_forcing_rows": [{
        "position": 0, "teacher_forced": True, "token_match": True,
    }]}, min_rows=1)
    assert teacher["passed"] is True


def test_evidence_contract_normalizes_existing_runtime_shapes():
    performance = rsi_evidence.validate_paired({
        "baseline_samples": [{"elapsed_s": 2.0}] * 5,
        "candidate_samples": [{"elapsed_s": 1.0}] * 5,
    }, min_runs=5)
    assert performance["passed"] is True and performance["runs"] == 5
    teacher = rsi_evidence.validate_teacher_forcing({
        "prompts": [{"positions": [
            {"position": 0, "teacher_forced": True, "mismatch": 0},
            {"position": 1, "teacher_forced": True, "mismatch": 0},
        ]}],
    })
    assert teacher["passed"] is True and teacher["positions"] == 2


def test_workspace_applies_patch_outside_source_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    (source / "value.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "value.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=source, check=True)
    workspace = rsi_workspace.ResearchWorkspace(source, "candidate-1")
    path = workspace.apply_patch(
        "diff --git a/value.txt b/value.txt\n"
        "--- a/value.txt\n+++ b/value.txt\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    assert (path / "value.txt").read_text(encoding="utf-8") == "new\n"
    assert (source / "value.txt").read_text(encoding="utf-8") == "old\n"
    workspace.cleanup()
    assert not path.exists()
