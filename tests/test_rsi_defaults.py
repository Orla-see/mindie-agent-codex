import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mindie-agent/scripts"
sys.path.insert(0, str(SCRIPTS))

import rsi_defaults  # noqa: E402


def test_default_profile_and_scope_are_explicit():
    defaults = rsi_defaults.default_settings({})
    assert defaults["profile_binding"]["tool"] == "msprof"
    assert defaults["optimization_scope"]["parameters"]["num_speculative_tokens"] == "adaptive"
    assert defaults["optimization_scope"]["strategy"] == "trend_bound"


def test_missing_policies_and_scope_produce_questions():
    fields = {item["field"] for item in rsi_defaults.questions({})}
    assert {"performance_policy", "accuracy_policy", "optimization_scope"} <= fields


def test_speculative_request_asks_for_method_and_weights():
    items = rsi_defaults.questions({"optimization_request": "优化投机解码"})
    fields = {item["field"] for item in items}
    assert {"speculative_method", "speculative_weights"} <= fields


def test_selected_method_and_weights_remove_followup_questions():
    items = rsi_defaults.questions({
        "optimization_request": "优化 dflash",
        "speculative_method": "dflash",
        "speculative_weights": "/models/dflash",
    })
    fields = {item["field"] for item in items}
    assert "speculative_method" not in fields
    assert "speculative_weights" not in fields
