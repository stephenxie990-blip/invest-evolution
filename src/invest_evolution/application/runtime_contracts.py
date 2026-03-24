"""Runtime contract catalog, artifact readers, and derivative tooling."""

from __future__ import annotations

import argparse
import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from invest_evolution.agent_runtime.presentation import build_contract_transcript_snapshots
from invest_evolution.agent_runtime.runtime import enforce_path_within_root
from invest_evolution.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

CONTRACT_PRIMARY_ENTRYPOINT = "/api/chat"
CONTRACTS_DIR = PROJECT_ROOT / "docs" / "contracts"
CONTRACT_PATH = CONTRACTS_DIR / "runtime-api-contract.v2.json"
SCHEMA_PATH = CONTRACTS_DIR / "runtime-api-contract.v2.schema.json"
OPENAPI_PATH = CONTRACTS_DIR / "runtime-api-contract.v2.openapi.json"

RUNTIME_V2_CONTRACT_PATH = CONTRACT_PATH
RUNTIME_V2_SCHEMA_PATH = SCHEMA_PATH
RUNTIME_V2_OPENAPI_PATH = OPENAPI_PATH


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
        id="runtime-v2",
        format="json",
        kind="runtime-api-contract",
        path="/api/contracts/runtime-v2",
        source_path=RUNTIME_V2_CONTRACT_PATH,
        shell_mount=CONTRACT_PRIMARY_ENTRYPOINT,
        not_found_error="runtime contract not found",
        load_error_log="Failed to load runtime API contract",
    ),
    RuntimeContractDocument(
        id="runtime-v2-schema",
        format="json-schema",
        kind="runtime-api-contract-derivative",
        path="/api/contracts/runtime-v2/schema",
        source_path=RUNTIME_V2_SCHEMA_PATH,
        shell_mount=CONTRACT_PRIMARY_ENTRYPOINT,
        not_found_error="runtime contract schema not found",
        load_error_log="Failed to load runtime API contract schema",
    ),
    RuntimeContractDocument(
        id="runtime-v2-openapi",
        format="openapi+json",
        kind="runtime-api-contract-derivative",
        path="/api/contracts/runtime-v2/openapi",
        source_path=RUNTIME_V2_OPENAPI_PATH,
        shell_mount=CONTRACT_PRIMARY_ENTRYPOINT,
        not_found_error="runtime contract openapi not found",
        load_error_log="Failed to load runtime API contract openapi",
    ),
)

RUNTIME_CONTRACT_DOCUMENTS_BY_ID = {item.id: item for item in RUNTIME_CONTRACT_DOCUMENTS}
RUNTIME_CONTRACT_PUBLIC_PATHS = frozenset(item.path for item in RUNTIME_CONTRACT_DOCUMENTS)


def _bounded_tail(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    normalized_limit = int(limit)
    if normalized_limit <= 0:
        return []
    return rows[-normalized_limit:]


def artifact_read_roots(runtime: Any) -> list[Path]:
    if runtime is None or not hasattr(runtime, "cfg"):
        return []
    cfg = runtime.cfg
    roots = [
        Path(cfg.training_output_dir),
        Path(cfg.artifact_log_dir),
        Path(cfg.config_snapshot_dir),
        Path(cfg.config_audit_log_path).parent,
        Path(cfg.training_plan_dir),
        Path(cfg.training_run_dir),
        Path(cfg.training_eval_dir),
    ]
    deduped: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in deduped:
            deduped.append(resolved)
    return deduped


def resolve_runtime_artifact_path(runtime: Any, path_str: str) -> Path | None:
    if runtime is None or not hasattr(runtime, "cfg"):
        return None
    raw = str(path_str or "").strip()
    if not raw:
        return None
    raw_path = Path(raw).expanduser()
    for root in artifact_read_roots(runtime):
        try:
            candidate = (
                enforce_path_within_root(root, raw_path)
                if raw_path.is_absolute()
                else enforce_path_within_root(root, root / raw_path)
            )
        except ValueError:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    logger.warning("Rejected artifact read outside runtime roots: %s", raw_path)
    return None


def safe_read_json(runtime: Any, path_str: str) -> Any:
    path = resolve_runtime_artifact_path(runtime, path_str)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read JSON artifact %s: %s", path, exc)
        return None


def safe_read_text(runtime: Any, path_str: str, *, limit: int = 12000) -> str:
    path = resolve_runtime_artifact_path(runtime, path_str)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read text artifact %s: %s", path, exc)
        return ""


def safe_read_jsonl(
    runtime: Any,
    path_str: str,
    *,
    limit: int = 400,
) -> list[dict[str, Any]]:
    path = resolve_runtime_artifact_path(runtime, path_str)
    if path is None:
        return []
    rows: list[dict[str, Any]] = []
    invalid_lines = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                invalid_lines += 1
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read JSONL artifact %s: %s", path, exc)
        return []
    if invalid_lines:
        logger.warning("Skipped %d invalid JSONL row(s) while reading %s", invalid_lines, path)
    return _bounded_tail(rows, limit)


def load_runtime_contract_document(document: RuntimeContractDocument) -> dict[str, Any]:
    path = document.source_path
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("contract document must be a JSON object")
    return payload


def load_contract_source() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _body_ref_schema(body_ref: str, components: dict[str, Any]) -> dict[str, Any]:
    if body_ref == "object":
        return {"type": "object", "additionalProperties": True}
    if body_ref in components:
        return {"$ref": f"#/components/schemas/{body_ref}"}
    raise ValueError(f"Unresolved body_ref from contract: {body_ref}")


def _parameter_schema(param: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": param.get("type", "string")}
    if "enum" in param and param["enum"]:
        schema["enum"] = list(param["enum"])
    if "default" in param and param["default"] is not None:
        schema["default"] = param["default"]
    if "items" in param and param["items"] is not None:
        schema["items"] = deepcopy(param["items"])
    return schema


def _normalize_contract_source(source_contract: dict[str, Any]) -> dict[str, Any]:
    contract = deepcopy(source_contract)
    contract.pop("removed_web_shell_mount", None)

    compatibility = dict(contract.get("compatibility") or {})
    recommendation = str(compatibility.get("error_normalization_recommendation") or "")
    if recommendation:
        compatibility["error_normalization_recommendation"] = recommendation.replace(
            "Frontend SDK",
            "Client SDK",
        )
        contract["compatibility"] = compatibility

    return contract


def validate_contract_references(contract: dict[str, Any]) -> None:
    components = {
        **deepcopy(contract["components"]["error_schemas"]),
        **deepcopy(contract["components"]["schemas"]),
    }
    allowed_refs = set(components)
    allowed_refs.add("object")
    allowed_refs.add("text/event-stream")
    unresolved_refs = sorted(
        {
            str(body_ref)
            for endpoint in list(contract.get("endpoints") or [])
            for body_ref in (
                []
                if str(dict(endpoint.get("success") or {}).get("content_type") or "application/json")
                == "text/event-stream"
                else [dict(endpoint.get("success") or {}).get("body_ref")]
            )
            if body_ref not in allowed_refs
        }
        | {
            str(error.get("body_ref"))
            for endpoint in list(contract.get("endpoints") or [])
            for error in list(endpoint.get("errors") or [])
            if dict(error or {}).get("body_ref") not in allowed_refs
        }
    )
    if unresolved_refs:
        raise ValueError(
            "Unresolved runtime contract body_ref(s): "
            + ", ".join(unresolved_refs)
        )


def build_openapi(contract: dict[str, Any]) -> dict[str, Any]:
    components = {
        **deepcopy(contract["components"]["error_schemas"]),
        **deepcopy(contract["components"]["schemas"]),
    }
    for name, payload in contract["components"]["sse_schemas"].items():
        component_name = f"Sse{name[0].upper()}{name[1:]}Data"
        components[component_name] = deepcopy(payload["data"])

    paths: dict[str, Any] = {}
    for endpoint in contract["endpoints"]:
        path_item = paths.setdefault(endpoint["path"], {})
        success_content_type = str(
            endpoint["success"].get("content_type") or "application/json"
        )
        success_body_ref = str(endpoint["success"].get("body_ref") or "")
        success_schema = (
            {"type": "string"}
            if success_content_type == "text/event-stream" or success_body_ref == "text/event-stream"
            else _body_ref_schema(success_body_ref, components)
        )
        operation: dict[str, Any] = {
            "operationId": endpoint["id"].replace(".", "_"),
            "tags": [endpoint.get("group", "default")],
            "summary": endpoint.get("summary", ""),
            "description": (
                "\n".join(endpoint.get("notes", []))
                if endpoint.get("notes")
                else endpoint.get("summary", "")
            ),
            "responses": {
                str(endpoint["success"]["http_status"]): {
                    "description": endpoint.get("summary", "Success"),
                    "content": {
                        success_content_type: {
                            "schema": success_schema,
                        }
                    },
                }
            },
            "x-runtime-required": bool(endpoint.get("runtime_required", False)),
            "x-runtime-preferred": bool(endpoint.get("runtime_preferred", False)),
            "x-latency": endpoint.get("latency", "unknown"),
            "x-realtime": bool(endpoint.get("realtime", False)),
            "x-pagination": endpoint.get("pagination", "none"),
        }
        if success_content_type == "text/event-stream" and endpoint.get("sse_event_refs"):
            operation["x-sse-event-refs"] = list(endpoint.get("sse_event_refs") or [])

        parameters = []
        for param in endpoint.get("query_params", []):
            parameters.append(
                {
                    "name": param["name"],
                    "in": "query",
                    "required": bool(param.get("required", False)),
                    "schema": _parameter_schema(param),
                    "description": param.get("description", ""),
                }
            )
        for param in endpoint.get("path_params", []):
            parameters.append(
                {
                    "name": param["name"],
                    "in": "path",
                    "required": True,
                    "schema": _parameter_schema(param),
                    "description": param.get("description", ""),
                }
            )
        if parameters:
            operation["parameters"] = parameters
        if endpoint.get("request_body") is not None:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": deepcopy(endpoint["request_body"]),
                    }
                },
            }
        for error in endpoint.get("errors", []):
            operation["responses"][str(error["http_status"])] = {
                "description": "; ".join(error.get("cases", [])) or "Error response",
                "content": {
                    "application/json": {
                        "schema": _body_ref_schema(error["body_ref"], components),
                    }
                },
            }
        path_item[endpoint["method"].lower()] = operation

    sse_event_refs = list(contract["sse"].get("event_refs", []))
    paths[contract["sse"]["path"]] = {
        "get": {
            "operationId": "events_stream",
            "tags": ["events"],
            "summary": "Consume the runtime SSE stream for training and agent observability.",
            "description": (
                "Server-Sent Events endpoint used by API, CLI, and agent clients "
                "for cycle, agent, module log, and meeting speech updates."
            ),
            "responses": {
                "200": {
                    "description": "SSE stream established.",
                    "content": {
                        "text/event-stream": {
                            "schema": {"type": "string"},
                        }
                    },
                }
            },
            "x-sse-event-refs": sse_event_refs,
            "x-sse-protocol": deepcopy(contract["sse"].get("protocol", {})),
            "x-runtime-required": False,
            "x-runtime-preferred": True,
            "x-realtime": True,
        }
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "投资进化系统 Runtime Contract",
            "version": contract["version"],
            "description": (
                f"Derived OpenAPI document generated from docs/contracts/{CONTRACT_PATH.name}"
            ),
        },
        "servers": [
            {"url": "/", "description": "Project root; paths remain absolute and unversioned."},
        ],
        "paths": paths,
        "components": {
            "schemas": components,
        },
        "x-transcript-snapshots": deepcopy(contract.get("transcript_snapshots", {})),
    }


def build_contract_schema() -> dict[str, Any]:
    schema_fragment = {"type": "object"}
    param_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string"},
            "required": {"type": "boolean"},
            "default": {},
            "enum": {"type": "array", "items": {}},
            "description": {"type": "string"},
            "items": {"type": "object"},
        },
        "required": ["name", "type"],
        "additionalProperties": True,
    }
    endpoint_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "group": {"type": "string"},
            "method": {"type": "string"},
            "path": {"type": "string"},
            "summary": {"type": "string"},
            "runtime_required": {"type": "boolean"},
            "runtime_preferred": {"type": "boolean"},
            "replacement": {"type": ["string", "null"]},
            "query_params": {"type": "array", "items": param_schema},
            "path_params": {"type": "array", "items": param_schema},
            "request_body": {"type": ["object", "null"]},
            "success": {
                "type": "object",
                "properties": {
                    "http_status": {"type": "integer"},
                    "body_ref": {"type": "string"},
                },
                "required": ["http_status", "body_ref"],
                "additionalProperties": True,
            },
            "errors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "http_status": {"type": "integer"},
                        "body_ref": {"type": "string"},
                        "cases": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["http_status", "body_ref", "cases"],
                    "additionalProperties": True,
                },
            },
            "latency": {"type": "string"},
            "pagination": {"type": "string"},
            "realtime": {"type": "boolean"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "sse_event_refs": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "id",
            "group",
            "method",
            "path",
            "summary",
            "runtime_required",
            "runtime_preferred",
            "replacement",
            "query_params",
            "path_params",
            "request_body",
            "success",
            "errors",
            "latency",
            "pagination",
            "realtime",
            "notes",
        ],
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://contracts.local/runtime-api-contract.v2.schema.json",
        "title": "Runtime API Contract V2",
        "type": "object",
        "properties": {
            "contract_id": {"type": "string", "const": "runtime-v2"},
            "version": {"type": "string"},
            "published_at": {"type": "string"},
            "api_base": {"type": "string"},
            "runtime_entrypoint": {"type": "string"},
            "contract_endpoint": {"type": "string"},
            "goals": {"type": "array", "items": {"type": "string"}},
            "compatibility": {
                "type": "object",
                "additionalProperties": {
                    "type": ["string", "boolean", "number", "null", "object", "array"]
                },
            },
            "preferred_runtime_flows": {
                "type": "object",
                "additionalProperties": {"type": "array", "items": {"type": "string"}},
            },
            "components": {
                "type": "object",
                "properties": {
                    "error_schemas": {
                        "type": "object",
                        "additionalProperties": schema_fragment,
                    },
                    "schemas": {
                        "type": "object",
                        "additionalProperties": schema_fragment,
                    },
                    "sse_schemas": {
                        "type": "object",
                        "additionalProperties": schema_fragment,
                    },
                },
                "required": ["error_schemas", "schemas", "sse_schemas"],
                "additionalProperties": False,
            },
            "sse": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content_type": {"type": "string"},
                    "protocol": {"type": "object", "additionalProperties": True},
                    "event_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["path", "content_type", "protocol", "event_refs"],
                "additionalProperties": False,
            },
            "endpoints": {"type": "array", "items": endpoint_schema},
            "transcript_snapshots": {
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "examples": {
                        "type": "object",
                        "additionalProperties": {"type": "object"},
                    },
                },
                "required": ["schema_version", "examples"],
                "additionalProperties": False,
            },
        },
        "required": [
            "contract_id",
            "version",
            "published_at",
            "api_base",
            "runtime_entrypoint",
            "contract_endpoint",
            "goals",
            "compatibility",
            "preferred_runtime_flows",
            "components",
            "sse",
            "endpoints",
            "transcript_snapshots",
        ],
        "additionalProperties": False,
    }


def build_contract_documents(
    source_contract: dict[str, Any] | None = None,
) -> dict[Path, dict[str, Any]]:
    contract = _normalize_contract_source(source_contract or load_contract_source())
    validate_contract_references(contract)
    contract["transcript_snapshots"] = build_contract_transcript_snapshots()
    schema = build_contract_schema()
    openapi = build_openapi(contract)
    return {
        CONTRACT_PATH: contract,
        SCHEMA_PATH: schema,
        OPENAPI_PATH: openapi,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_contract_documents(
    source_contract: dict[str, Any] | None = None,
) -> dict[Path, dict[str, Any]]:
    documents = build_contract_documents(source_contract)
    for path, payload in documents.items():
        write_json(path, payload)
    return documents


def check_contract_documents(source_contract: dict[str, Any] | None = None) -> list[str]:
    documents = build_contract_documents(source_contract)
    drift: list[str] = []
    for path, expected in documents.items():
        if not path.exists():
            drift.append(f"missing: {path}")
            continue
        current = json.loads(path.read_text(encoding="utf-8"))
        if current != expected:
            drift.append(f"drift: {path}")
    return drift


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh or verify runtime API contract artifacts."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify generated artifacts are up to date.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.check:
        drift = check_contract_documents()
        if drift:
            for item in drift:
                print(item)
            return 1
        print("runtime contract artifacts are up to date")
        return 0

    write_contract_documents()
    print("refreshed runtime contract artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
