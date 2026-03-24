import importlib
import warnings

from invest_evolution.common import environment


def test_collect_environment_issues_reports_missing_modules(monkeypatch):
    monkeypatch.setattr(
        environment.importlib.util,
        "find_spec",
        lambda name: None if name == "rank_bm25" else object(),
    )

    issues = environment.collect_environment_issues(required_modules=["pandas", "rank_bm25"])

    assert [issue.code for issue in issues] == ["missing_module"]
    assert issues[0].message == "missing required module: rank_bm25"


def test_collect_environment_issues_captures_requests_dependency_warning(monkeypatch):
    class RequestsDependencyWarning(Warning):
        pass

    monkeypatch.setattr(environment.importlib.util, "find_spec", lambda _name: object())

    real_import_module = importlib.import_module

    def _fake_import_module(name: str):
        if name == "requests":
            warnings.warn(
                "urllib3/chardet mismatch",
                RequestsDependencyWarning,
            )
            return object()
        return real_import_module(name)

    monkeypatch.setattr(environment.importlib, "import_module", _fake_import_module)

    issues = environment.collect_environment_issues(validate_requests_stack=True)

    assert [issue.code for issue in issues] == ["unsupported_requests_stack"]
    assert "urllib3/chardet mismatch" in issues[0].message
