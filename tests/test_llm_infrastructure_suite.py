from pathlib import Path
from types import SimpleNamespace

# LLM Infrastructure Imports
from invest_evolution.common.utils import LLMGateway
from invest_evolution.common.utils import LLMRouter
from invest_evolution.investment.shared.llm import LLMCaller
from invest_evolution.investment.evolution.analysis import LLMOptimizer
from invest_evolution.config import EvolutionConfig

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# --- Helper Classes ---

class _DummyMessage:
    def __init__(self, content='ok'): self.content = content

class _DummyChoice:
    def __init__(self, content='ok'): self.message = _DummyMessage(content)

class _DummyResp:
    def __init__(self, choices): self.choices = choices


# --- LLM Gateway Tests ---

def test_llm_gateway_normalizes_gpt5_temperature():
    gateway = LLMGateway(model="gpt-5-mini", api_key="token", api_base="https://example.com")
    kwargs = gateway._build_completion_kwargs([], 0.2, 128)
    assert kwargs["temperature"] == 1.0
    assert kwargs["model"] == "openai/gpt-5-mini"


def test_llm_gateway_retries_on_empty_choices(monkeypatch):
    gateway = LLMGateway(model="gpt-5-mini", api_key="token", api_base="https://example.com", max_retries=2)
    calls = {"n": 0}
    class DummyLiteLLM:
        @staticmethod
        def completion(**kwargs):
            calls["n"] += 1
            return _DummyResp([]) if calls["n"] == 1 else _DummyResp([_DummyChoice('ok')])
    import invest_evolution.common.utils as mod
    monkeypatch.setattr(mod, 'litellm', DummyLiteLLM)
    resp = gateway.completion_raw(messages=[{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=16)
    assert resp.choices[0].message.content == 'ok'


# --- LLM Optimizer & Orchestrator Tests ---

def test_parse_response_falls_back_on_dry_run_payload():
    optimizer = LLMOptimizer()
    result = optimizer._parse_response('{"dry_run": true}', {"cycle_id": 1})
    assert "表现不佳" in result.cause
    assert result.runtime_adjustments.get("stop_loss_pct") == 0.05


def test_optimizer_default_path_uses_controlled_factory(monkeypatch):
    fake_caller = SimpleNamespace(call=lambda **kwargs: '{"cause":"回撤","runtime_adjustments":{"position_size":0.1}}')
    monkeypatch.setattr("invest_evolution.investment.evolution.analysis.resolve_default_llm", lambda kind: SimpleNamespace(model="m", api_key="k", api_base="b"))
    monkeypatch.setattr("invest_evolution.investment.evolution.analysis.build_component_llm_caller", lambda k, **kw: fake_caller)
    optimizer = LLMOptimizer()
    assert optimizer.llm is fake_caller


# --- LLM Parser (Shared) Tests ---

def test_parse_fenced_json_with_newlines_and_quotes():
    raw = """```json
{
  "verdict": "avoid",
  "bull_summary": "第一行\n第二行",
  "reasoning": "MACD\"金叉\""
}
```"""
    parsed = LLMCaller.parse_json_text(raw)
    assert parsed["verdict"] == "avoid"
    assert "第一行" in parsed["bull_summary"]


# --- LLM Router Tests ---

def test_llm_router_behavior_from_config():
    cfg = EvolutionConfig()
    cfg.llm_fast_model = "model-a"
    cfg.llm_deep_model = "model-a"
    router = LLMRouter.from_config(cfg, dry_run=True)
    assert router.fast() is router.deep()
    
    cfg.llm_deep_model = "model-b"
    router2 = LLMRouter.from_config(cfg, dry_run=True)
    assert router2.fast() is not router2.deep()


def test_dry_run_routing_returns_stats():
    router = LLMRouter.from_config(EvolutionConfig(), dry_run=True)
    stats = router.get_stats()
    assert all(k in stats for k in ["fast", "deep", "shared"])
    assert "dry_run" in router.fast().call("s", "u")
