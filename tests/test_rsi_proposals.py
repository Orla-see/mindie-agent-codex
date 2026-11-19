import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mindie-agent/scripts"
sys.path.insert(0, str(SCRIPTS))

import rsi_proposals  # noqa: E402


def test_adaptive_scope_emits_trend_bound_probe_without_fixed_range():
    result = rsi_proposals.propose({
        "optimization_scope": {
            "kind": "runtime_parameters",
            "strategy": "trend_bound",
            "parameters": {"num_speculative_tokens": "adaptive"},
        },
        "profile_observations": [
            {"num_speculative_tokens": 2, "throughput_tokens_s": 100},
            {"num_speculative_tokens": 4, "throughput_tokens_s": 115},
            {"num_speculative_tokens": 8, "throughput_tokens_s": 130},
        ],
    })
    assert len(result) == 1
    assert result[0]["config"]["search"] == "trend_bound"
    assert result[0]["trend"]["status"] == "expand"
