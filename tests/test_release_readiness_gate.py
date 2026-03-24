import pytest

from scripts import run_release_readiness_gate as readiness_module


def test_shadow_probe_rejects_non_sample_verify_overrides():
    with pytest.raises(SystemExit) as excinfo:
        readiness_module.main(
            [
                "--list",
                "--include-shadow-gate",
                "--shadow-verify-unexpected-reject-count-max",
                "1",
            ]
        )

    assert excinfo.value.code == 2


def test_shadow_probe_rejects_negative_sample_verify_overrides():
    with pytest.raises(SystemExit) as excinfo:
        readiness_module.main(
            [
                "--list",
                "--include-shadow-gate",
                "--shadow-verify-successful-cycles-min",
                "-1",
            ]
        )

    assert excinfo.value.code == 2
