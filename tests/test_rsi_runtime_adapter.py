import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mindie-agent/scripts"
sys.path.insert(0, str(SCRIPTS))

import rsi_runtime_adapter  # noqa: E402
import rsi_install  # noqa: E402


def test_reference_adapter_never_uses_shell_and_parses_json(tmp_path):
    command = tmp_path / "command.py"
    command.write_text(
        "import json,sys\n"
        "payload=json.load(sys.stdin)\n"
        "print('diagnostic line')\n"
        "print(json.dumps({'status':'completed','passed':True,'benchmark_passed':True,\n"
        "'paired_speedup_ci_lower_bound':1.2,'request_count':4}))\n",
        encoding="utf-8",
    )
    payload = {
        "runtime_binding": {"commands": {"benchmark_cmd": [sys.executable, str(command)]}},
        "candidate": {"candidate_id": "c1"},
    }
    result = rsi_runtime_adapter.dispatch("performance", tmp_path, payload)
    assert result["passed"] is True
    assert result["paired_speedup_ci_lower_bound"] == 1.2


def test_plan_test_command_is_used_as_performance_argv(tmp_path):
    command = tmp_path / "command.py"
    command.write_text("import json; print(json.dumps({'status':'completed','passed':True}))\n",
                       encoding="utf-8")
    result = rsi_runtime_adapter.dispatch(
        "performance", tmp_path,
        {"test_command": [sys.executable, str(command)]},
    )
    assert result["passed"] is True


def test_reference_adapter_missing_evidence_fails_closed(tmp_path):
    result = rsi_runtime_adapter.dispatch("accuracy", tmp_path, {})
    assert result["status"] == "unavailable"
    assert result["passed"] is False
    assert result["reason"] == "accuracy_command_not_configured"


def test_simulation_adapter_normalizes_analytical_metrics(tmp_path):
    command = tmp_path / "simulator.py"
    command.write_text(
        "import json\n"
        "print(json.dumps({'status':'completed','throughput':120,'ttft_ms':8,'oom':False}))\n",
        encoding="utf-8",
    )
    result = rsi_runtime_adapter.dispatch(
        "simulation", tmp_path,
        {"objective": "maximize throughput",
         "runtime_binding": {"commands": {"simulation_cmd": [sys.executable, str(command)]}}},
    )
    assert result["evidence_kind"] == "analytical"
    assert result["eligible_for_measurement"] is True
    assert result["simulation_score"] == 120


def test_simulation_adapter_reads_standard_output_artifacts(tmp_path):
    output = tmp_path / "sim-output"
    output.mkdir()
    (output / "predict_dict.json").write_text(
        json.dumps({"decode": {"throughput_tokens_s": 88}}), encoding="utf-8"
    )
    (output / "memory_dict.json").write_text(
        json.dumps({"rank_0": {"oom": True}}), encoding="utf-8"
    )
    command = tmp_path / "silent_simulator.py"
    command.write_text("print('simulation complete')\n", encoding="utf-8")
    result = rsi_runtime_adapter.dispatch(
        "simulation", tmp_path,
        {"objective": "maximize throughput", "simulation_output_dir": str(output),
         "runtime_binding": {"commands": {"simulation_cmd": [sys.executable, str(command)]}}},
    )
    assert result["metrics"]["throughput_tokens_s"] == 88
    assert result["oom"] is True
    assert result["eligible_for_measurement"] is False


def test_implementation_provider_uses_configured_claude_command(tmp_path):
    command = tmp_path / "claude_stub.py"
    command.write_text(
        "import json,sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'result':'```diff\\n*** Begin Patch\\n*** Update File: vllm_ascend/example.py\\n@@\\n-old\\n+new\\n*** End Patch\\n```'}))\n",
        encoding="utf-8",
    )
    result = rsi_runtime_adapter.dispatch(
        "implement", tmp_path,
        {"runtime_binding": {"implementation_provider": "claude-code",
                             "implementation_providers": {
                                 "claude-code": {"command": [sys.executable, str(command)]},
                             }},
         "candidate": {"candidate_id": "c1"}},
    )
    assert result["implementation_provider"] == "claude"
    assert result["patch"].startswith("*** Begin Patch")


def test_implementation_provider_detects_claude_without_codex(monkeypatch):
    monkeypatch.setattr(rsi_runtime_adapter.shutil, "which",
                        lambda name: "/usr/bin/claude" if name == "claude" else None)
    command, provider = rsi_runtime_adapter._implementation_command({}, {}, None)
    assert provider == "claude"
    assert command == ["claude", "-p", "--output-format", "json", "--input-format", "text"]


def test_reference_adapter_wrapper_round_trip(tmp_path):
    root = tmp_path
    rsi_install.install(root)
    bindings = root / "config/live-runtime-bindings.json"
    config = json.loads(bindings.read_text(encoding="utf-8"))
    config["default"]["runtime_python"] = sys.executable
    bindings.write_text(json.dumps(config), encoding="utf-8")
    process = subprocess.run(
        [sys.executable, str(root / "bin/mindie_agent_rsi_entry.py"), "prepare", "--root", str(root)],
        input=json.dumps({"candidate": {"candidate_id": "c1"}}),
        text=True, capture_output=True, check=False,
    )
    assert process.returncode == 0
    assert json.loads(process.stdout)["candidate_id"] == "c1"
