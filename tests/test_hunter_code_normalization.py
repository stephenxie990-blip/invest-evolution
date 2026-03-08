from invest.agents.hunters import _normalize_candidate_code, _recover_hunter_result


def test_normalize_candidate_code_accepts_equivalent_formats():
    valid = ["sh.600058", "sz.000001"]
    assert _normalize_candidate_code("600058", valid) == "sh.600058"
    assert _normalize_candidate_code("SH600058", valid) == "sh.600058"
    assert _normalize_candidate_code("sz000001", valid) == "sz.000001"

def test_recover_hunter_result_from_truncated_json():
    raw = '{"picks":[{"code":"600058","score":0.82,"reasoning":"趋势延续","stop_loss_pct":0.05,"take_profit_pct":0.18},{"code":"sz000001","score":0.74,"reasoning":"量价改善","stop_loss_pct":0.04,"take_profit_pct":0.15}],"overall_view":"候选质量较好","confidence":0.68'
    recovered = _recover_hunter_result(raw, ["sh.600058", "sz.000001"], 0.05, 0.15)

    assert [item["code"] for item in recovered["picks"]] == ["sh.600058", "sz.000001"]
    assert recovered["confidence"] == 0.68
    assert recovered["overall_view"] == "候选质量较好"

