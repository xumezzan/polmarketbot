from app.services.operator import (
    _extract_market_matching_context,
    _find_risk_decision,
    _find_signal_snapshot,
)


def test_find_signal_snapshot_returns_matching_signal() -> None:
    raw_response = {
        "snapshots": {
            "signal_engine": {
                "signals": [
                    {"signal_id": 10, "candidate": {"match_score": 0.31}},
                    {"signal_id": 11, "candidate": {"match_score": 0.52}},
                ]
            }
        }
    }

    snapshot = _find_signal_snapshot(raw_response=raw_response, signal_id=11)

    assert snapshot is not None
    assert snapshot["candidate"]["match_score"] == 0.52


def test_find_risk_decision_returns_matching_signal() -> None:
    raw_response = {
        "snapshots": {
            "risk_engine": {
                "decisions": [
                    {"signal_id": 10, "allow": False, "blockers": ["a"]},
                    {"signal_id": 11, "allow": True, "blockers": []},
                ]
            }
        }
    }

    decision = _find_risk_decision(raw_response=raw_response, signal_id=11)

    assert decision is not None
    assert decision["allow"] is True


def test_extract_market_matching_context_returns_candidate_count_and_delta() -> None:
    raw_response = {
        "snapshots": {
            "market_matching": {
                "candidate_count": 3,
                "candidates": [
                    {"match_score": 0.62},
                    {"match_score": 0.57},
                    {"match_score": 0.31},
                ],
            }
        }
    }

    candidate_count, top_delta = _extract_market_matching_context(raw_response)

    assert candidate_count == 3
    assert top_delta == 0.05


def test_extract_market_matching_context_handles_missing_second_candidate() -> None:
    raw_response = {
        "snapshots": {
            "market_matching": {
                "candidate_count": 1,
                "candidates": [{"match_score": 0.62}],
            }
        }
    }

    candidate_count, top_delta = _extract_market_matching_context(raw_response)

    assert candidate_count == 1
    assert top_delta is None
