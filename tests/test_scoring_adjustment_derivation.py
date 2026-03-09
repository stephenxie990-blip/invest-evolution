from app.train import SelfLearningController
from invest.evolution.llm_optimizer import AnalysisResult


def test_controller_can_derive_scoring_adjustments_from_analysis(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / 'out'),
        meeting_log_dir=str(tmp_path / 'meetings'),
        config_audit_log_path=str(tmp_path / 'state' / 'audit.jsonl'),
        config_snapshot_dir=str(tmp_path / 'state' / 'snapshots'),
    )
    controller.model_name = 'mean_reversion'
    controller.investment_model.model_name = 'mean_reversion'
    analysis = AnalysisResult(
        cause='反弹持续性存疑，近期追高后亏损',
        suggestions=['减少交易频率', '增加趋势确认'],
        strategy_adjustments={'position_size': 0.15},
        new_strategy_needed=False,
    )
    adjustments = controller._derive_scoring_adjustments(analysis, analysis.strategy_adjustments)
    assert 'penalties' in adjustments or 'weights' in adjustments
