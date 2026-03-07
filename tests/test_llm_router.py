"""
Tests for LLMRouter (Phase 1)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_llm_router_from_config():
    """LLMRouter.from_config() 能正确创建双轨 caller"""
    from config import EvolutionConfig
    from llm_router import LLMRouter
    from core import LLMCaller

    cfg = EvolutionConfig()
    router = LLMRouter.from_config(cfg, dry_run=True)

    assert isinstance(router.fast(), LLMCaller)
    assert isinstance(router.deep(), LLMCaller)


def test_llm_router_shared_when_same_model():
    """当 fast 和 deep 模型相同时，router.fast() is router.deep() 成立"""
    from config import EvolutionConfig
    from llm_router import LLMRouter

    cfg = EvolutionConfig()
    cfg.llm_fast_model = "model-a"
    cfg.llm_deep_model = "model-a"

    router = LLMRouter.from_config(cfg, dry_run=True)
    assert router.fast() is router.deep(), "相同模型时应共享同一 caller"


def test_llm_router_separate_when_different_models():
    """当 fast 和 deep 模型不同时，返回不同实例"""
    from config import EvolutionConfig
    from llm_router import LLMRouter

    cfg = EvolutionConfig()
    cfg.llm_fast_model = "model-fast"
    cfg.llm_deep_model = "model-deep"

    router = LLMRouter.from_config(cfg, dry_run=True)
    assert router.fast() is not router.deep(), "不同模型时应为独立 caller"


def test_llm_router_get_stats():
    """get_stats() 返回正确结构"""
    from config import EvolutionConfig
    from llm_router import LLMRouter

    cfg = EvolutionConfig()
    router = LLMRouter.from_config(cfg, dry_run=True)
    stats = router.get_stats()

    assert "fast" in stats
    assert "deep" in stats
    assert "shared" in stats


def test_llm_router_from_caller():
    """LLMRouter.from_caller() 向后兼容"""
    from core import LLMCaller
    from llm_router import LLMRouter

    caller = LLMCaller(dry_run=True)
    router = LLMRouter.from_caller(caller)

    assert router.fast() is caller
    assert router.deep() is caller


def test_dry_run_routing():
    """dry_run 模式下 fast 和 deep 均能正常返回占位响应"""
    from config import EvolutionConfig
    from llm_router import LLMRouter

    cfg = EvolutionConfig()
    router = LLMRouter.from_config(cfg, dry_run=True)

    fast_response = router.fast().call("sys", "user")
    deep_response = router.deep().call("sys", "user")

    assert "dry_run" in fast_response
    assert "dry_run" in deep_response
