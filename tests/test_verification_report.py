from invest_evolution.application.verification_report import GateResult, REPORT_PATH, build_report, generate_commands


def fake_runner(name: str, command):
    return GateResult(name=name, command=tuple(command), returncode=0, stdout="ok", stderr="")


def test_build_report_with_stub(tmp_path, monkeypatch):
    monkeypatch.chdir(REPORT_PATH.parent.parent)
    report = build_report(runner=fake_runner)
    assert isinstance(report.get("generated_at"), str)
    assert report.get("git_sha")
    assert len(report.get("results", [])) == len(generate_commands())
    assert REPORT_PATH.exists()
