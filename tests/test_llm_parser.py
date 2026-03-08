from invest.shared.llm import LLMCaller


def test_parse_fenced_json_with_newlines_inside_strings():
    raw = """```json
{
  "verdict": "avoid",
  "confidence": 0.65,
  "bull_summary": "第一行
第二行",
  "bear_summary": "技术面偏弱",
  "reasoning": "综合判断"
}
```"""
    parsed = LLMCaller.parse_json_text(raw)
    assert parsed["verdict"] == "avoid"
    assert parsed["confidence"] == 0.65
    assert "第一行" in parsed["bull_summary"]

def test_parse_fenced_json_with_inner_quotes_in_strings():
    raw = """```json
{
  "verdict": "hold",
  "confidence": 0.55,
  "bull_summary": "MACD"金叉"、均线多头排列",
  "bear_summary": "估值偏高",
  "reasoning": "等待更清晰信号"
}
```"""
    parsed = LLMCaller.parse_json_text(raw)
    assert parsed["verdict"] == "hold"
    assert parsed["confidence"] == 0.55
    assert "MACD" in parsed["bull_summary"]

