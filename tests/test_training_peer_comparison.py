from invest_evolution.application.training.research import compare_candidate_to_peers, select_peer_candidates


def test_select_peer_candidates_keeps_same_market_tag_and_limits_count():
    peers = select_peer_candidates(
        [
            {"manager_id": "a", "market_tag": "bull", "score": 8.0, "sample_count": 5, "active": True},
            {"manager_id": "b", "market_tag": "bullish", "score": 7.0, "sample_count": 4, "active": True},
            {"manager_id": "c", "market_tag": "bear", "score": 9.0, "sample_count": 8, "active": True},
            {"manager_id": "d", "market_tag": "bull", "score": 6.0, "sample_count": 3, "active": True},
            {"manager_id": "e", "market_tag": "bull", "score": 5.0, "sample_count": 2, "active": False},
        ],
        market_tag="bull",
        max_peers=2,
    )

    assert [item["manager_id"] for item in peers] == ["a", "b"]


def test_compare_candidate_to_peers_detects_peer_dominance():
    result = compare_candidate_to_peers(
        {"manager_id": "candidate", "score": 6.0, "avg_return_pct": 0.3, "benchmark_pass_rate": 0.4},
        [
            {"manager_id": "peer_a", "market_tag": "bull", "score": 8.0, "avg_return_pct": 0.5, "benchmark_pass_rate": 0.7, "sample_count": 5},
            {"manager_id": "peer_b", "market_tag": "bull", "score": 7.0, "avg_return_pct": 0.4, "benchmark_pass_rate": 0.6, "sample_count": 5},
        ],
        market_tag="bull",
    )

    assert result.comparable is True
    assert result.peer_dominated is True
    assert result.dominant_peer == "peer_a"
    assert "peer_dominated" in result.reason_codes


def test_compare_candidate_to_peers_handles_no_comparable_peers():
    result = compare_candidate_to_peers(
        {"manager_id": "candidate", "score": 6.0, "avg_return_pct": 0.3, "benchmark_pass_rate": 0.4},
        [{"manager_id": "peer_a", "market_tag": "bear", "score": 8.0, "sample_count": 5}],
        market_tag="bull",
    )

    assert result.comparable is False
    assert result.compared_count == 0
    assert result.reason_codes == ["insufficient_evidence"]
