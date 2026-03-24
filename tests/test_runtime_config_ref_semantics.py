import pandas as pd

from invest_evolution.investment.runtimes import MomentumRuntime


def _make_stock_data():
    return {
        "sh.600000": pd.DataFrame(
            {
                "date": ["2023-05-29", "2023-05-30", "2023-05-31", "2023-06-01"],
                "trade_date": ["20230529", "20230530", "20230531", "20230601"],
                "open": [10.0, 10.1, 10.2, 10.3],
                "high": [10.2, 10.3, 10.4, 10.5],
                "low": [9.9, 10.0, 10.1, 10.2],
                "close": [10.1, 10.2, 10.3, 10.4],
                "volume": [1000000.0, 1100000.0, 1200000.0, 1300000.0],
                "pct_chg": [0.0, 0.99, 0.98, 0.97],
            }
        )
    }


def test_runtime_process_emits_canonical_manager_config_ref_across_output_channels():
    runtime = MomentumRuntime()

    output = runtime.process(_make_stock_data(), "20230601")
    expected_ref = str(runtime.config.path)

    assert output.manager_config_ref == expected_ref
    assert output.signal_packet.manager_config_ref == expected_ref
    assert output.agent_context.manager_config_ref == expected_ref
