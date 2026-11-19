import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mindie-agent/scripts"
sys.path.insert(0, str(SCRIPTS))

import rsi_trend  # noqa: E402


def test_trend_bound_detects_compute_saturation():
    result = rsi_trend.assess_bound([
        {"num_speculative_tokens": 2, "throughput_tokens_s": 100, "compute_utilization": 92},
        {"num_speculative_tokens": 4, "throughput_tokens_s": 100.5, "compute_utilization": 94},
        {"num_speculative_tokens": 8, "throughput_tokens_s": 100.2, "compute_utilization": 95},
    ])
    assert result["bound"] is True
    assert result["reason"] == "compute_bound"


def test_trend_bound_keeps_expanding_when_improving():
    result = rsi_trend.assess_bound([
        {"num_speculative_tokens": 2, "throughput_tokens_s": 100},
        {"num_speculative_tokens": 4, "throughput_tokens_s": 115},
        {"num_speculative_tokens": 8, "throughput_tokens_s": 130},
    ])
    assert result["status"] == "expand"
    assert result["next_action"] == "probe_wider"
