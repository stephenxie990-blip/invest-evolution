from __future__ import annotations

import pandas as pd
import pytest

import invest_evolution.application.commander.ops as services_module


class _DummyQueryService:
    def get_capital_flow(self, **_kwargs):
        return pd.DataFrame([{"code": "sh.600001"}, {"code": "sh.600002"}])

    def get_dragon_tiger_events(self, **_kwargs):
        return pd.DataFrame([{"code": "sh.600001"}, {"code": "sh.600002"}])

    def get_intraday_60m_bars(self, **_kwargs):
        return pd.DataFrame([{"code": "sh.600001"}, {"code": "sh.600002"}])


@pytest.mark.parametrize(
    "builder_name",
    [
        "get_capital_flow_payload",
        "get_dragon_tiger_payload",
        "get_intraday_60m_payload",
    ],
)
def test_market_payload_builders_allow_zero_limit(monkeypatch, builder_name: str):
    monkeypatch.setattr(services_module, "MarketQueryService", lambda: _DummyQueryService())
    builder = getattr(services_module, builder_name)

    payload = builder(limit=0)

    assert payload["count"] == 0
    assert payload["items"] == []

    default_payload = builder(limit=None)

    assert default_payload["count"] == 2
    assert len(default_payload["items"]) == 2
