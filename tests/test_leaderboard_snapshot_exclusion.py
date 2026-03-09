from pathlib import Path
import json

from invest.leaderboard.engine import collect_cycle_records


def test_collect_cycle_records_excludes_config_snapshots(tmp_path: Path):
    (tmp_path / 'cycle_0001_config_snapshot.json').write_text(json.dumps({'initial_capital': 100000}), encoding='utf-8')
    (tmp_path / 'cycle_1.json').write_text(json.dumps({'cycle_id': 1, 'model_name': 'momentum', 'config_name': 'momentum_v1', 'params': {}, 'self_assessment': {}, 'return_pct': 1.0}), encoding='utf-8')
    records = collect_cycle_records(tmp_path)
    assert len(records) == 1
    assert records[0]['cycle_id'] == 1
