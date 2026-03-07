"""
IndustryRegistry 单元测试

覆盖：
  - JSON 加载正确
  - get_industry() 命中 / 未命中
  - register() 动态注册
  - 消费端 (RiskFactorModel, PortfolioRiskManager, TradingAnalyzer) 共享同一数据
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IndustryRegistry, industry_registry, DATA_DIR


# ============================================================
# IndustryRegistry 自身
# ============================================================

class TestIndustryRegistry:

    def test_json_loaded(self):
        """默认 JSON 被正确加载"""
        mapping = industry_registry.all()
        assert isinstance(mapping, dict)
        assert len(mapping) > 0
        assert "sh.600519" in mapping
        assert mapping["sh.600519"] == "白酒"

    def test_get_industry_hit(self):
        """已知代码 → 正确行业"""
        assert industry_registry.get_industry("sh.600036") == "银行"
        assert industry_registry.get_industry("sz.300750") == "新能源"
        assert industry_registry.get_industry("sh.600276") == "医药"

    def test_get_industry_miss(self):
        """未知代码 → '其他'"""
        assert industry_registry.get_industry("xx.999999") == "其他"
        assert industry_registry.get_industry("") == "其他"

    def test_register_new(self):
        """动态注册新映射"""
        reg = IndustryRegistry(json_path=DATA_DIR / "industry_map.json")
        reg.register("sz.999999", "测试行业")
        assert reg.get_industry("sz.999999") == "测试行业"

    def test_register_override(self):
        """动态覆盖已有映射"""
        reg = IndustryRegistry(json_path=DATA_DIR / "industry_map.json")
        original = reg.get_industry("sh.600519")
        assert original == "白酒"
        reg.register("sh.600519", "食品饮料")
        assert reg.get_industry("sh.600519") == "食品饮料"

    def test_all_returns_copy(self):
        """all() 返回副本，修改不影响原数据"""
        copy = industry_registry.all()
        copy["sh.600519"] = "已修改"
        assert industry_registry.get_industry("sh.600519") == "白酒"

    def test_missing_json_path(self):
        """JSON 文件不存在 → 空映射，不崩溃"""
        reg = IndustryRegistry(json_path=Path("/tmp/nonexistent.json"))
        assert reg.all() == {}
        assert reg.get_industry("sh.600519") == "其他"


# ============================================================
# 消费端共享验证
# ============================================================

class TestConsumersUseRegistry:

    def test_risk_factor_model(self):
        """RiskFactorModel.get_industry() 走注册表"""
        from optimization import RiskFactorModel
        model = RiskFactorModel()
        assert model.get_industry("sh.600036") == "银行"
        assert model.get_industry("sz.300750") == "新能源"
        assert model.get_industry("xx.999999") == "其他"

    def test_portfolio_risk_manager(self):
        """PortfolioRiskManager.get_industry() 走注册表"""
        from trading import PortfolioRiskManager
        mgr = PortfolioRiskManager()
        assert mgr.get_industry("sh.601398") == "银行"
        assert mgr.get_industry("sh.600276") == "医药"
        assert mgr.get_industry("xx.999999") == "其他"

    def test_trading_analyzer(self):
        """TradingAnalyzer.get_industry() 走注册表"""
        from optimization import TradingAnalyzer
        analyzer = TradingAnalyzer()
        assert analyzer.get_industry("sh.600519") == "白酒"
        assert analyzer.get_industry("xx.999999") == "其他"

    def test_all_consumers_consistent(self):
        """三个消费端对同一代码返回相同行业"""
        from optimization import RiskFactorModel, TradingAnalyzer
        from trading import PortfolioRiskManager

        code = "sh.600036"
        results = {
            RiskFactorModel().get_industry(code),
            PortfolioRiskManager().get_industry(code),
            TradingAnalyzer().get_industry(code),
        }
        assert len(results) == 1, f"不一致: {results}"
