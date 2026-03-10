from app.llm_gateway import LLMGateway


def test_llm_gateway_normalizes_gpt5_temperature():
    gateway = LLMGateway(model="gpt-5.4", api_key="token", api_base="https://example.com")
    kwargs = gateway._build_completion_kwargs([], 0.2, 128)
    assert kwargs["temperature"] == 1.0


def test_llm_gateway_keeps_non_gpt5_temperature():
    gateway = LLMGateway(model="minimax/MiniMax-M2.5-highspeed", api_key="token", api_base="https://example.com")
    kwargs = gateway._build_completion_kwargs([], 0.2, 128)
    assert kwargs["temperature"] == 0.2


class _DummyMessage:
    def __init__(self, content='ok'):
        self.content = content


class _DummyChoice:
    def __init__(self, content='ok'):
        self.message = _DummyMessage(content)


class _DummyResp:
    def __init__(self, choices):
        self.choices = choices


def test_llm_gateway_retries_on_empty_choices(monkeypatch):
    gateway = LLMGateway(model="gpt-5.4", api_key="token", api_base="https://example.com", max_retries=2)

    calls = {"n": 0}

    class DummyLiteLLM:
        @staticmethod
        def completion(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _DummyResp([])
            return _DummyResp([_DummyChoice('ok')])

    import app.llm_gateway as mod
    monkeypatch.setattr(mod, 'litellm', DummyLiteLLM)
    resp = gateway.completion_raw(messages=[{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=16)
    assert resp.choices[0].message.content == 'ok'
    assert calls["n"] == 2


def test_llm_gateway_hard_timeout_fails_fast(monkeypatch):
    gateway = LLMGateway(model="gpt-5.4", api_key="token", api_base="https://example.com", timeout=1, max_retries=3)

    calls = {"n": 0}

    class DummyLiteLLM:
        @staticmethod
        def completion(**kwargs):
            import time
            calls["n"] += 1
            time.sleep(2.5)
            return _DummyResp([_DummyChoice('late')])

    import app.llm_gateway as mod
    monkeypatch.setattr(mod, 'litellm', DummyLiteLLM)
    monkeypatch.setenv('INVEST_LLM_HARD_TIMEOUT_GRACE', '0')

    import pytest
    from app.llm_gateway import LLMGatewayError

    with pytest.raises(LLMGatewayError, match='timeout'):
        gateway.completion_raw(messages=[{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=16)
    assert calls["n"] == 1
