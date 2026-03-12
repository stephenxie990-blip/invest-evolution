import json
from pathlib import Path

from app.frontend_contract_tools import (
    CONTRACT_PATH,
    OPENAPI_PATH,
    SCHEMA_PATH,
    build_contract_documents,
    check_contract_documents,
)


def test_generated_frontend_contract_artifacts_have_no_drift():
    assert check_contract_documents() == []


def test_generated_frontend_contract_documents_match_repo_files():
    generated = build_contract_documents()
    current = {
        CONTRACT_PATH: json.loads(Path(CONTRACT_PATH).read_text(encoding='utf-8')),
        SCHEMA_PATH: json.loads(Path(SCHEMA_PATH).read_text(encoding='utf-8')),
        OPENAPI_PATH: json.loads(Path(OPENAPI_PATH).read_text(encoding='utf-8')),
    }

    assert generated == current
