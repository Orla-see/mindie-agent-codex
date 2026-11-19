import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mindie-agent/scripts"
sys.path.insert(0, str(SCRIPTS))

import rsi_knowledge  # noqa: E402
import rsi_proposals  # noqa: E402


def test_algorithm_intent_returns_at_most_six_ranked_knowledge_candidates():
    payload = {
        "optimization_scope": {"kind": "algorithm", "strategy": "knowledge_rank"},
        "optimization_request": "优化 DFlash 的投机推理，并考虑动态接受率",
        "runtime_capabilities": {"runtime_supports_dflash": True},
        "profile_observations": [
            {"acceptance_rate": 0.25}, {"acceptance_rate": 0.80},
        ],
    }
    result = rsi_proposals.propose(payload, {"largest_gap": "verify"})
    assert 1 <= len(result) <= 6
    assert result[0]["method"].startswith("knowledge:")
    assert "estimated_gain_pct" in result[0]
    assert result[0]["research_request"]["source_types"] == [
        "repository", "academic_paper", "configured_feed"
    ]
    card = result[0]["implementation_card"]
    assert card["schema"] == "mindie-agent/implementation-card/v1"
    assert "vllm_ascend" in card["repository_mapping"]["preferred_directories"]
    assert card["patch_contract"]["format"] == "unified_diff"


def test_query_shapes_are_normalized_and_unknown_requirements_are_explicit():
    cards = rsi_knowledge.extract_cards({"foundation_cards": [{
        "unit_id": "unit-dflash", "title": "DFlash", "refs": ["arxiv:2602.06036"],
        "expected_gain_pct": {"low": 4, "high": 12},
    }]})
    ranked = rsi_knowledge.rank_cards(cards, {"objective": "throughput"}, {})
    assert ranked[0]["card"]["id"] == "unit-dflash"
    assert ranked[0]["unknown_requirements"] == []


def test_refresh_policy_is_due_and_never_auto_distills_unverified_text():
    value = rsi_knowledge.refresh_policy({"knowledge_refresh": {
        "interval_seconds": 3600, "last_sync_unix": 100,
    }}, now=3701)
    assert value["refresh_due"] is True
    assert value["auto_distill"] == "verified_sources_only"
