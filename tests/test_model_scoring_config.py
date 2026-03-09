from pathlib import Path
import tempfile

import yaml

from invest.models import MeanReversionModel, DefensiveLowVolModel


def _write_temp_config(base_path: str, patch: dict) -> str:
    data = yaml.safe_load(Path(base_path).read_text(encoding='utf-8'))
    for key, value in patch.items():
        data[key] = value
    fd = tempfile.NamedTemporaryFile(delete=False, suffix='.yaml')
    Path(fd.name).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return fd.name


def test_mean_reversion_score_responds_to_scoring_config():
    item = {
        'rsi': 20.0,
        'bb_pos': 0.10,
        'change_5d': -8.0,
        'change_20d': -12.0,
        'vol_ratio': 1.2,
        'volatility': 0.02,
        'ma_trend': '空头',
    }
    base = MeanReversionModel()
    boosted_cfg = _write_temp_config(
        'invest/models/configs/mean_reversion_v1.yaml',
        {
            'scoring': {
                'weights': {
                    'oversold_rsi': 0.80,
                    'lower_bb': 0.40,
                    'drop_5d': 0.30,
                    'drop_20d': 0.20,
                    'bearish_trend_bonus': 0.10,
                    'volume_ratio_bonus': 0.10,
                },
                'bands': {
                    'lower_bb_threshold': 0.35,
                    'upper_bb_threshold': 0.80,
                    'vol_ratio_low': 0.8,
                    'vol_ratio_high': 1.8,
                    'high_volatility_threshold': 0.05,
                },
                'penalties': {
                    'upper_bb': 0.10,
                    'insufficient_drop_5d': 0.05,
                    'insufficient_drop_20d': 0.05,
                    'high_volatility': 0.08,
                    'overheat_rsi': 0.15,
                },
            }
        },
    )
    boosted = MeanReversionModel(config_path=boosted_cfg)
    assert boosted._reversion_score(item) > base._reversion_score(item)


def test_defensive_score_responds_to_penalty_config():
    item = {
        'volatility': 0.02,
        'rsi': 80.0,
        'change_20d': -5.0,
        'change_5d': -6.0,
        'bb_pos': 0.9,
        'vol_ratio': 0.5,
        'ma_trend': '空头',
    }
    base = DefensiveLowVolModel()
    harsher_cfg = _write_temp_config(
        'invest/models/configs/defensive_low_vol_v1.yaml',
        {
            'scoring': {
                'weights': {
                    'low_volatility': 0.35,
                    'preferred_rsi': 0.20,
                    'soft_rsi': 0.08,
                    'change_20d_band': 0.15,
                    'change_5d_band': 0.10,
                    'bullish_trend': 0.12,
                    'bb_band': 0.05,
                    'volume_ratio_band': 0.03,
                },
                'bands': {
                    'rsi_soft_low': 35.0,
                    'rsi_soft_high': 70.0,
                    'change_5d_low': -2.0,
                    'change_5d_high': 4.0,
                    'bb_pos_low': 0.35,
                    'bb_pos_high': 0.75,
                    'vol_ratio_low': 0.8,
                    'vol_ratio_high': 1.5,
                },
                'penalties': {
                    'bad_rsi': 0.20,
                    'weak_change_20d': 0.18,
                    'bearish_trend': 0.15,
                },
            }
        },
    )
    harsher = DefensiveLowVolModel(config_path=harsher_cfg)
    assert harsher._defensive_score(item) < base._defensive_score(item)
