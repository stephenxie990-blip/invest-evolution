from __future__ import annotations

import pandas as pd

from market_data.pit_join import join_financials_point_in_time


def test_join_financials_point_in_time_uses_asof_publish_date():
    bars = pd.DataFrame(
        [
            {"code": "sh.600010", "trade_date": "20240429", "close": 10.0},
            {"code": "sh.600010", "trade_date": "20240515", "close": 10.2},
            {"code": "sh.600010", "trade_date": "20240521", "close": 10.5},
            {"code": "sz.000001", "trade_date": "20240201", "close": 20.0},
        ]
    )
    financials = pd.DataFrame(
        [
            {
                "code": "sh.600010",
                "report_date": "20231231",
                "publish_date": "20240430",
                "roe": 11.0,
                "net_profit": 100.0,
                "source": "tushare",
            },
            {
                "code": "sh.600010",
                "report_date": "20240331",
                "publish_date": "20240520",
                "roe": 13.0,
                "net_profit": 120.0,
                "source": "tushare",
            },
            {
                "code": "sz.000001",
                "report_date": "20240131",
                "publish_date": "",
                "roe": 9.0,
                "net_profit": 50.0,
                "source": "tushare",
            },
        ]
    )

    result = join_financials_point_in_time(bars, financials)

    first = result.loc[(result["code"] == "sh.600010") & (result["trade_date"] == "20240429")].iloc[0]
    assert pd.isna(first["financial_report_date"])
    assert pd.isna(first["roe"])

    mid = result.loc[(result["code"] == "sh.600010") & (result["trade_date"] == "20240515")].iloc[0]
    assert mid["financial_report_date"] == "20231231"
    assert mid["financial_publish_date"] == "20240430"
    assert float(mid["roe"]) == 11.0

    last = result.loc[(result["code"] == "sh.600010") & (result["trade_date"] == "20240521")].iloc[0]
    assert last["financial_report_date"] == "20240331"
    assert last["financial_publish_date"] == "20240520"
    assert float(last["roe"]) == 13.0

    other = result.loc[(result["code"] == "sz.000001") & (result["trade_date"] == "20240201")].iloc[0]
    assert other["financial_report_date"] == "20240131"
    assert other["financial_publish_date"] == ""
    assert float(other["roe"]) == 9.0
