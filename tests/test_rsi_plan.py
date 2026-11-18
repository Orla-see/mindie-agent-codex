import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mindie-agent/scripts"
sys.path.insert(0, str(SCRIPTS))

import rsi_plan  # noqa: E402


def base_plan():
    return {
        "schema": rsi_plan.SCHEMA,
        "task_id": "decode-latency",
        "endpoint": {"host": "80.5.25.139", "port": 22, "user": "root"},
        "source_root": "/home/user/model",
        "rsi_root": "/home/user/RSI",
        "baseline": "baseline-control",
        "objective": "maximize throughput",
        "operations": ["doctor", "profile", "accuracy", "performance"],
        "parallel_workers": 2,
        "devices": ["0", "1"],
        "payload": {"sealed_final": True},
    }


def test_validate_normalizes_defaults_and_render_requires_lease():
    plan = rsi_plan.validate(base_plan())
    assert plan["endpoint"]["user"] == "root"
    assert plan["action_entry"] == "bin/mindie_agent_rsi_entry.py"
    rendered = rsi_plan.render(plan)
    assert rendered["steps"][0]["tool"] == "remote_write"
    assert json.loads(rendered["steps"][0]["arguments"]["content"])["source_root"] == "/home/user/model"
    assert rendered["steps"][2]["requires"] == ["coordinator.lease"]
    assert rendered["gates"]["sealed_final"] is True


def test_custom_action_entry_is_relative_to_rsi_root():
    plan = base_plan()
    plan["action_entry"] = "rsi-mindie/bin/mindie_agent_rsi_entry.py"
    rendered = rsi_plan.render(plan)
    commands = [step["arguments"]["cmd"] for step in rendered["steps"][1:]]
    assert any("python3 rsi-mindie/bin/mindie_agent_rsi_entry.py profile" in cmd
               for cmd in commands)


def test_action_entry_cannot_escape_rsi_root():
    plan = base_plan()
    plan["action_entry"] = "../bin/mindie_agent_rsi_entry.py"
    with pytest.raises(rsi_plan.PlanError, match="relative entry path"):
        rsi_plan.validate(plan)


@pytest.mark.parametrize("field", ["source_root", "rsi_root"])
def test_remote_paths_cannot_escape_root(field):
    plan = base_plan()
    plan[field] = "/home/user/../secret"
    with pytest.raises(rsi_plan.PlanError, match="absolute remote path"):
        rsi_plan.validate(plan)


def test_shell_payload_is_rejected():
    plan = base_plan()
    plan["payload"] = {"command": "rm -rf /"}
    with pytest.raises(rsi_plan.PlanError, match="shell fields"):
        rsi_plan.validate(plan)


def test_cli_render_is_machine_readable(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(base_plan()), encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPTS / "rsi_plan.py"), "render", str(path)],
                            check=True, capture_output=True, text=True)
    body = json.loads(result.stdout)
    assert body["status"] == "valid"
    assert body["plan"]["steps"][1]["tool"] == "remote_exec_command"
