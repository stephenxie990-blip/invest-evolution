from market_data.gateway import MarketDataGateway


class _FakeService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def sync_security_master(self):
        return {'stock_count': 1}

    def sync_daily_bars(self):
        return {'row_count': 2}

    def sync_index_bars(self):
        return {'row_count': 3}

    def sync_trading_calendar(self, start_date, end_date):
        return {'source': 'baostock', 'start_date': start_date, 'end_date': end_date}

    def sync_trading_calendar_from_akshare(self, start_date, end_date):
        return {'source': 'akshare', 'start_date': start_date, 'end_date': end_date}


class _FakeLoader:
    pass



def test_market_data_gateway_background_sync_uses_single_gateway():
    gateway = MarketDataGateway(ingestion_factory=_FakeService, runtime_policy={})

    payload = gateway.sync_background_full_refresh()

    assert payload == {
        'security': {'stock_count': 1},
        'daily': {'row_count': 2},
        'index': {'row_count': 3},
    }



def test_market_data_gateway_disables_online_loader_by_policy():
    gateway = MarketDataGateway(
        runtime_policy={'allow_online_fallback': False, 'allow_capital_flow_sync': False},
        online_loader_factory=_FakeLoader,
    )

    loader, error = gateway.create_online_loader()

    assert loader is None
    assert error == 'disabled_by_control_plane'



def test_market_data_gateway_routes_calendar_sync_by_source():
    gateway = MarketDataGateway(ingestion_factory=_FakeService, runtime_policy={})

    baostock = gateway.sync_calendar(source='baostock', start_date='20240101', end_date='20240131')
    akshare = gateway.sync_calendar(source='akshare', start_date='20240101', end_date='20240131')

    assert baostock['source'] == 'baostock'
    assert akshare['source'] == 'akshare'
