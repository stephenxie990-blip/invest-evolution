"""Shared helpers for serving runtime contract documents through the interface layer."""

from __future__ import annotations

from typing import Any

from flask import jsonify

from app.runtime_contract_catalog import (
    RUNTIME_CONTRACT_DOCUMENTS_BY_ID,
    build_runtime_contract_catalog_items,
    load_runtime_contract_document,
)


def build_runtime_contracts_payload() -> dict[str, Any]:
    items = build_runtime_contract_catalog_items()
    return {"count": len(items), "items": items}


def serve_runtime_contract_document(
    document_id: str,
    *,
    logger: Any,
    load_document: Any = load_runtime_contract_document,
):
    document = RUNTIME_CONTRACT_DOCUMENTS_BY_ID[document_id]
    try:
        return jsonify(load_document(document))
    except FileNotFoundError:
        return jsonify({"error": document.not_found_error}), 404
    except Exception as exc:
        logger.exception(document.load_error_log)
        return jsonify({"error": str(exc)}), 500
