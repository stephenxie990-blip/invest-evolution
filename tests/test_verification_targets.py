from invest_evolution.application.verification_targets import RESEARCH_FEEDBACK_TESTS, focused_protocol_tests


def test_research_feedback_tests_included():
    all_targets = set(focused_protocol_tests(include_research=True))
    for expected in RESEARCH_FEEDBACK_TESTS:
        assert expected in all_targets
