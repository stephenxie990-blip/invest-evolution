from invest.evolution.llm_optimizer import AnalysisResult
from invest.evolution.scoring_policy import derive_scoring_adjustments


def test_controller_can_derive_scoring_adjustments_from_analysis():
    analysis = AnalysisResult(
        cause='反弹持续性存疑，近期追高后亏损',
        suggestions=['减少交易频率', '增加趋势确认'],
        strategy_adjustments={'position_size': 0.15},
        new_strategy_needed=False,
    )
    adjustments = derive_scoring_adjustments('mean_reversion', analysis)
    assert 'penalties' in adjustments or 'weights' in adjustments
