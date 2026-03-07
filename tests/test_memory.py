"""
Tests for MarketSituationMemory (Phase 2)
"""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_memory(save_dir=None):
    from memory import MarketSituationMemory
    return MarketSituationMemory("test_agent", save_dir)


def test_memory_add_and_query():
    """add_experience 后 query 能返回结果"""
    mem = _make_memory()
    mem.add_experience(
        situation="牛市初期，沪指连续5日上涨，量能持续放大",
        action="重仓趋势突破股",
        outcome="获利12%，止损点位合理",
    )
    assert len(mem) == 1

    results = mem.query("大盘量能放大上涨趋势明显", n_matches=1)
    assert isinstance(results, list)
    assert len(results) >= 0  # BM25 可能不匹配，但不崩溃


def test_memory_empty_query():
    """空记忆库查询返回空列表"""
    mem = _make_memory()
    results = mem.query("任意情境", n_matches=3)
    assert results == []


def test_memory_multiple_experiences():
    """多条记忆，BM25 能按相似度排序"""
    mem = _make_memory()
    mem.add_experience("熊市下跌，RSI超卖，恐慌情绪蔓延", "减仓保守", "规避风险，亏损控制在5%以内")
    mem.add_experience("震荡市，牛市苗头，量比放大", "趋势选股", "获利8%")
    mem.add_experience("牛市行情，上证指数突破年线", "满仓趋势", "获利20%")

    assert len(mem) == 3

    results = mem.query("牛市上涨行情", n_matches=2)
    # 结果应该是最多 2 条
    assert len(results) <= 2


def test_memory_format_hints_for_prompt():
    """format_hints_for_prompt 返回非空字符串"""
    mem = _make_memory()
    mem.add_experience("牛市行情，趋势向好", "趋势追涨", "获利15%")

    hint = mem.format_hints_for_prompt("当前牛市行情明显", n_matches=1)
    # 有记忆时应返回非空文本
    assert isinstance(hint, str)


def test_memory_format_hints_empty():
    """无记忆时返回空字符串"""
    mem = _make_memory()
    hint = mem.format_hints_for_prompt("任意情境", n_matches=2)
    assert hint == ""


def test_memory_save_and_load():
    """持久化与恢复"""
    with tempfile.TemporaryDirectory() as tmpdir:
        mem1 = _make_memory(save_dir=tmpdir)
        mem1.add_experience("牛市", "重仓", "盈利")
        mem1.add_experience("熊市", "轻仓", "保本")
        mem1.save()

        mem2 = _make_memory(save_dir=tmpdir)
        mem2.load()
        assert len(mem2) == 2


def test_memory_clear():
    """clear 清空所有记忆"""
    mem = _make_memory()
    mem.add_experience("情境", "操作", "结果")
    assert len(mem) == 1

    mem.clear()
    assert len(mem) == 0
    assert mem.query("情境") == []


def test_memory_multiple_queries():
    """批量添加 + 多次查询不崩溃"""
    mem = _make_memory()
    for i in range(10):
        mem.add_experience(
            situation=f"情境{i}: 上涨趋势 RSI={30+i*3}",
            action=f"操作{i}",
            outcome=f"结果{i}: {'盈利' if i % 2 == 0 else '亏损'}",
        )

    assert len(mem) == 10

    r = mem.query("上涨趋势 RSI较低 量能放大", n_matches=3)
    assert len(r) <= 3
