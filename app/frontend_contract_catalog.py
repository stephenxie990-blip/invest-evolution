"""Shared metadata and loaders for frontend contract documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.frontend_contract_tools import CONTRACT_PATH, OPENAPI_PATH, SCHEMA_PATH
from app.web_ui_metadata import FRONTEND_APP_ROUTE


@dataclass(frozen=True)
class FrontendContractDocument:
    id: str
    format: str
    kind: str
    path: str
    source_path: Path
    shell_mount: str
    not_found_error: str
    load_error_log: str

    def to_catalog_item(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "format": self.format,
            "kind": self.kind,
            "path": self.path,
            "source_path": str(self.source_path),
            "shell_mount": self.shell_mount,
        }


FRONTEND_CONTRACT_DOCUMENTS = (
    FrontendContractDocument(
        id="frontend-v1",
        format="json",
        kind="frontend-api-contract",
        path="/api/contracts/frontend-v1",
        source_path=CONTRACT_PATH,
        shell_mount=FRONTEND_APP_ROUTE,
        not_found_error="frontend contract not found",
        load_error_log="Failed to load frontend API contract",
    ),
    FrontendContractDocument(
        id="frontend-v1-schema",
        format="json-schema",
        kind="frontend-api-contract-derivative",
        path="/api/contracts/frontend-v1/schema",
        source_path=SCHEMA_PATH,
        shell_mount=FRONTEND_APP_ROUTE,
        not_found_error="frontend contract schema not found",
        load_error_log="Failed to load frontend API contract schema",
    ),
    FrontendContractDocument(
        id="frontend-v1-openapi",
        format="openapi+json",
        kind="frontend-api-contract-derivative",
        path="/api/contracts/frontend-v1/openapi",
        source_path=OPENAPI_PATH,
        shell_mount=FRONTEND_APP_ROUTE,
        not_found_error="frontend contract openapi not found",
        load_error_log="Failed to load frontend API contract openapi",
    ),
)

FRONTEND_CONTRACT_DOCUMENTS_BY_ID = {item.id: item for item in FRONTEND_CONTRACT_DOCUMENTS}
FRONTEND_CONTRACT_PUBLIC_PATHS = frozenset(
    {"/api/contracts", *(item.path for item in FRONTEND_CONTRACT_DOCUMENTS)}
)


def build_frontend_contract_catalog_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for document in FRONTEND_CONTRACT_DOCUMENTS:
        if document.source_path.exists():
            items.append(document.to_catalog_item())
    return items


def load_frontend_contract_document(document: FrontendContractDocument) -> dict[str, Any]:
    path = document.source_path
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("contract document must be a JSON object")
    return payload
