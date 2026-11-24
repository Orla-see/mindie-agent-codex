import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mindie-agent/scripts"
sys.path.insert(0, str(SCRIPTS))

import rsi_autotune  # noqa: E402
import rsi_plan  # noqa: E402


def plan(tmp_path, *, candidates=None):
    return {
        "schema": rsi_plan.SCHEMA,
        "task_id": "autotune-test",
        "endpoint": {"host": "npu.example", "port": 22},
        "source_root": "/workspace/source",
        "startup_script": "/workspace/source/start.sh",
        "test_script": "/workspace/source/benchmark.sh",
        # The plan schema intentionally accepts remote POSIX paths only;
        # pytest's local Windows temp directory is used solely for state.
        "rsi_root": "/workspace/rsi",
        "baseline": "baseline-control",
        "objective": "verified throughput",
        "operations": ["prepare", "build", "accuracy", "performance", "cleanup"],
        "parallel_workers": 2,
        "devices": ["0", "1"],
        "payload": {"candidates": candidates or [
            {"candidate_id": "c1", "method": "config", "config": {"k": 1}}
        ]},
    }


class ScriptedRunner:
    def __init__(self, *, speedup=1.2, fail=False, unknown=False):
        self.calls = []
        self.speedup = speedup
        self.fail = fail
        self.unknown = unknown

    def run(self, operation, payload):
        self.calls.append(operation)
        if operation == "build" and self.fail:
            return rsi_autotune.ActionReceipt(operation, {"status": "failed"})
        if operation == "performance" and self.unknown:
            return rsi_autotune.ActionReceipt(operation, {"status": "running"})
        if operation == "accuracy":
            return rsi_autotune.ActionReceipt(operation, {
                "status": "completed", "passed": True, "correctness_passed": True,
                "teacher_forced": True, "paper_ready": True,
            })
        if operation == "performance":
            return rsi_autotune.ActionReceipt(operation, {
                "status": "completed", "passed": True,
                "benchmark_passed": True, "sealed_final": True,
                "repeats": 3,
                "paired_speedup_ci_lower_bound": self.speedup,
            })
        return rsi_autotune.ActionReceipt(operation, {"status": "completed"})


def test_expand_candidates_is_deterministic_and_bounded():
    payload = {"candidate_method": "tree", "candidate_space": {"k": [1, 2], "depth": [4, 8]}}
    first = rsi_autotune.expand_candidates(payload)
    second = rsi_autotune.expand_candidates(payload)
    assert [x["candidate_id"] for x in first] == [x["candidate_id"] for x in second]
    assert len(first) == 4


def test_successful_candidate_is_promoted_only_after_all_gates(tmp_path):
    runner = ScriptedRunner()
    controller = rsi_autotune.AutotuneController(
        plan(tmp_path), runner, tmp_path / "state", workers=1,
        required_gates=rsi_autotune.DEFAULT_REQUIRED_GATES,
    )
    result = controller.run_once()
    assert result["best"]["candidate_id"] == "c1"
    assert json.loads((tmp_path / "state" / "active-best.json").read_text())["candidate_id"] == "c1"
    assert runner.calls == ["prepare", "build", "accuracy", "performance"]


def test_simulation_selects_top_k_before_real_measurement(tmp_path):
    value = plan(tmp_path, candidates=[
        {"candidate_id": "slow", "method": "config", "config": {"k": 1}},
        {"candidate_id": "fast", "method": "config", "config": {"k": 2}},
    ])
    value["operations"] = ["simulation", "prepare", "build", "accuracy", "performance", "cleanup"]
    value["payload"]["simulation_top_k"] = 1

    class SimulatorRunner(ScriptedRunner):
        def run(self, operation, payload):
            if operation == "simulation":
                self.calls.append(operation)
                candidate = payload["candidate"]["candidate_id"]
                return rsi_autotune.ActionReceipt(operation, {
                    "status": "completed", "passed": True,
                    "eligible_for_measurement": True,
                    "simulation_score": 20 if candidate == "fast" else 10,
                })
            return super().run(operation, payload)

    runner = SimulatorRunner()
    controller = rsi_autotune.AutotuneController(
        value, runner, tmp_path / "state", workers=2,
        required_gates=rsi_autotune.DEFAULT_REQUIRED_GATES,
    )
    first = controller.run_once()
    assert first["selected_for_measurement"] == ["fast"]
    second = controller.run_once()
    assert second["best"]["candidate_id"] == "fast"


def test_simulation_failure_keeps_candidates_pending(tmp_path):
    value = plan(tmp_path)
    value["operations"] = ["simulation", "performance"]

    class FailedSimulator:
        def run(self, operation, payload):
            return rsi_autotune.ActionReceipt(operation, {
                "status": "failed", "passed": False, "reason": "simulator_unavailable"
            })

    controller = rsi_autotune.AutotuneController(
        value, FailedSimulator(), tmp_path / "state", workers=1, required_gates=()
    )
    result = controller.run_once()
    assert result["status"] == "simulation_failed"
    queue = json.loads((tmp_path / "state" / "autotune-queue.json").read_text())
    assert queue["candidates"][0]["status"] == "pending"


def test_simulation_only_persists_global_ranking_without_measurement(tmp_path):
    candidate_plan = plan(tmp_path)
    candidate_plan["startup_script"] = None
    candidate_plan["test_script"] = None
    candidate_plan["operations"] = ["simulation"]
    candidate_plan["parallel_workers"] = 2
    candidate_plan["payload"] = {
        "candidate_method": "tree",
        "candidate_space": {"k": [1, 2, 3, 4]},
        "simulation_max_candidates": 2,
        "simulation_top_k": 2,
    }

    class SimulationOnlyRunner:
        def __init__(self):
            self.calls = []

        def run(self, operation, payload):
            self.calls.append(operation)
            candidate = payload["candidate"]
            score = candidate["config"]["k"]
            return rsi_autotune.ActionReceipt(operation, {
                "status": "completed", "passed": True,
                "evidence_kind": "analytical", "eligible_for_measurement": True,
                "simulation_score": score,
                "metrics": {"throughput_tokens_s": score},
            })

    runner = SimulationOnlyRunner()
    controller = rsi_autotune.AutotuneController(
        candidate_plan, runner, tmp_path / "state", workers=2, required_gates=(),
    )
    first = controller.run_once()
    second = controller.run_once()

    assert first["status"] == "simulation_progress"
    assert second["status"] == "simulation_completed"
    assert second["simulation_only"] is True
    assert second["selected_for_measurement"] == []
    assert len(second["ranking"]) == 4
    assert len(runner.calls) == 4
    result_path = tmp_path / "state" / "simulation-results.json"
    persisted = json.loads(result_path.read_text())
    assert persisted["schema"] == "mindie-agent/rsi-simulation-results/v1"
    assert persisted["selected_candidate_ids"] == [
        rsi_autotune.candidate_id("tree", {"k": 4}),
        rsi_autotune.candidate_id("tree", {"k": 3}),
    ]
    state = json.loads((tmp_path / "state" / "autotune-state.json").read_text())
    assert state["simulation_result_path"] == str(result_path)
    assert not (tmp_path / "state" / "active-best.json").exists()


def test_cli_stops_after_simulation_only_completion(tmp_path, monkeypatch, capsys):
    candidate_plan = plan(tmp_path)
    candidate_plan["startup_script"] = None
    candidate_plan["test_script"] = None
    candidate_plan["operations"] = ["simulation"]
    candidate_plan["payload"] = {"candidate_space": {"k": [1, 2]}}
    path = tmp_path / "simulation-plan.json"
    path.write_text(json.dumps(candidate_plan), encoding="utf-8")

    class FakeController:
        calls = 0

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self):
            type(self).calls += 1
            return {"status": "simulation_completed", "simulation_only": True,
                    "selected_candidates": ["candidate-1"]}

    monkeypatch.setattr(rsi_autotune, "AutotuneController", FakeController)
    assert rsi_autotune.main([
        str(path), "--dry-run", "--state-dir", str(tmp_path / "state"),
    ]) == 0
    assert FakeController.calls == 1
    assert json.loads(capsys.readouterr().out)["simulation_only"] is True


def test_failed_candidate_is_cleaned_and_does_not_replace_baseline(tmp_path):
    runner = ScriptedRunner(fail=True)
    controller = rsi_autotune.AutotuneController(plan(tmp_path), runner, tmp_path / "state")
    result = controller.run_once()
    assert result["best"]["candidate_id"] == "baseline-control"
    assert runner.calls[-1] == "cleanup"


def test_unknown_receipt_stops_promotion_without_retry(tmp_path):
    runner = ScriptedRunner(unknown=True)
    controller = rsi_autotune.AutotuneController(plan(tmp_path), runner, tmp_path / "state")
    result = controller.run_once()
    assert result["state"] == "needs_reconcile"
    assert result["best"]["candidate_id"] == "baseline-control"
    assert runner.calls == ["prepare", "build", "accuracy", "performance"]


def test_cli_dry_run_is_machine_readable(tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan(tmp_path)), encoding="utf-8")
    assert rsi_autotune.main([str(path), "--dry-run", "--once", "--state-dir", str(tmp_path / "state")]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "completed"
    assert body["best"]["candidate_id"] == "baseline-control"


def test_cli_requests_missing_startup_or_test_artifacts(tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan(tmp_path)), encoding="utf-8")
    assert rsi_autotune.main([str(path), "--state-dir", str(tmp_path / "state")]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "needs_input"
    assert "startup_script" in body["required_inputs"][0]


def test_cli_stops_when_target_is_reached_by_default(tmp_path, monkeypatch, capsys):
    class FakeController:
        calls = 0

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self):
            type(self).calls += 1
            return {"status": "completed", "best": {
                "candidate_id": f"c{type(self).calls}", "speedup": 1.07}}

    monkeypatch.setattr(rsi_autotune, "AutotuneController", FakeController)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan(tmp_path)), encoding="utf-8")
    assert rsi_autotune.main([
        str(path), "--dry-run", "--target-speedup", "1.07",
        "--state-dir", str(tmp_path / "state"),
    ]) == 0
    assert FakeController.calls == 1
    assert json.loads(capsys.readouterr().out)["best"]["speedup"] == 1.07


def test_cli_explicit_round_budget_keeps_running_after_target(tmp_path, monkeypatch, capsys):
    class FakeController:
        calls = 0

        def __init__(self, *args, **kwargs):
            pass

        def run_once(self):
            type(self).calls += 1
            return {"status": "completed", "best": {
                "candidate_id": f"c{type(self).calls}", "speedup": 1.07}}

    monkeypatch.setattr(rsi_autotune, "AutotuneController", FakeController)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan(tmp_path)), encoding="utf-8")
    assert rsi_autotune.main([
        str(path), "--dry-run", "--target-speedup", "1.07", "--max-rounds", "3",
        "--state-dir", str(tmp_path / "state"),
    ]) == 0
    assert FakeController.calls == 3
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["best"]["candidate_id"] == "c3"


def test_auto_proposer_persists_cost_model_and_memory(tmp_path):
    value = plan(tmp_path)
    value["payload"] = {
        "auto_propose": True,
        "max_candidates": 1,
        "profile_observations": [{
            "elapsed_ms": 1000, "completion_tokens": 100,
            "baseline_elapsed_ms": 1200, "baseline_tokens": 100,
            "verify_ms": 700,
        }],
    }
    runner = ScriptedRunner()
    controller = rsi_autotune.AutotuneController(value, runner, tmp_path / "state")
    result = controller.run_once()
    assert result["best"]["candidate_id"] != "baseline-control"
    assert json.loads((tmp_path / "state" / "cost-model.json").read_text())["largest_gap"] == "verify"
    assert json.loads((tmp_path / "state" / "memory.jsonl").read_text())["candidate_id"] == result["best"]["candidate_id"]


def test_sealed_final_payload_overrides_asserted_runtime_gate(tmp_path):
    value = plan(tmp_path)
    value["payload"]["sealed_final_payload"] = {
        "unseen_prompts": [{"id": "u1"}, {"id": "u2"}, {"id": "u3"}],
        "long_outputs": [{"id": "l1", "output_tokens": 1024}],
        "paired_speedups": [1.2, 1.25, 1.3],
        "token_forcing_rows": [{"teacher_forced": True, "token_match": False}],
    }
    runner = ScriptedRunner()
    controller = rsi_autotune.AutotuneController(value, runner, tmp_path / "state")
    result = controller.run_once()
    assert result["best"]["candidate_id"] == "baseline-control"
    candidate = next(iter(result["results"].values()))
    assert candidate["gates"]["sealed_final"] is False


def test_strict_evidence_rejects_asserted_runtime_passes(tmp_path):
    value = plan(tmp_path)
    value["payload"]["strict_evidence"] = True
    runner = ScriptedRunner()
    controller = rsi_autotune.AutotuneController(value, runner, tmp_path / "state")
    result = controller.run_once()
    assert result["best"]["candidate_id"] == "baseline-control"
    candidate = next(iter(result["results"].values()))
    assert candidate["gates"]["teacher_forced"] is False
    assert candidate["gates"]["paired_evidence"] is False
