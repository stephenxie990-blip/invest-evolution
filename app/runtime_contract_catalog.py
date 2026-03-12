"""Shared metadata and loaders for runtime contract documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime_contract_tools import CONTRACT_PATH, OPENAPI_PATH, SCHEMA_PATH


CONTRACT_PRIMARY_ENTRYPOINT = "/api/chat"


@dataclass(frozen=True)
class RuntimeContractDocument:
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


RUNTIME_CONTRACT_DOCUMENTS = (
    RuntimeContractDocument(
        id="runtime-v1",
        format="json",
        kind="runtime-api-contract",
        path="/api/contracts/runtime-v1",
        source_path=CONTRACT_PATH,
        shell_mount=CONTRACT_PRIMARY_ENTRYPOINT,
        not_found_error="runtime contract not found",
        load_error_log="Failed to load runtime API contract",
    ),
    RuntimeContractDocument(
        id="runtime-v1-schema",
        format="json-schema",
        kind="runtime-api-contract-derivative",
        path="/api/contracts/runtime-v1/schema",
        source_path=SCHEMA_PATH,
        shell_mount=CONTRACT_PRIMARY_ENTRYPOINT,
        not_found_error="runtime contract schema not found",
        load_error_log="Failed to load runtime API contract schema",
    ),
    RuntimeContractDocument(
        id="runtime-v1-openapi",
        format="openapi+json",
        kind="runtime-api-contract-derivative",
        path="/api/contracts/runtime-v1/openapi",
        source_path=OPENAPI_PATH,
        shell_mount=CONTRACT_PRIMARY_ENTRYPOINT,
        not_found_error="runtime contract openapi not found",
        load_error_log="Failed to load runtime API contract openapi",
    ),
)

RUNTIME_CONTRACT_DOCUMENTS_BY_ID = {item.id: item for item in RUNTIME_CONTRACT_DOCUMENTS}
RUNTIME_CONTRACT_PUBLIC_PATHS = frozenset(
    {"/api/contracts", *(item.path for item in RUNTIME_CONTRACT_DOCUMENTS)}
)


def build_runtime_contract_catalog_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for document in RUNTIME_CONTRACT_DOCUMENTS:
        if document.source_path.exists():
            items.append(document.to_catalog_item())
    return items


def load_runtime_contract_document(document: RuntimeContractDocument) -> dict[str, Any]:
    path = document.source_path
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("contract document must be a JSON object")
    return payload
