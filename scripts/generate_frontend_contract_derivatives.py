#!/usr/bin/env python3
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = PROJECT_ROOT / 'docs' / 'contracts'
CONTRACT_PATH = CONTRACTS_DIR / 'frontend-api-contract.v1.json'
SCHEMA_PATH = CONTRACTS_DIR / 'frontend-api-contract.v1.schema.json'
OPENAPI_PATH = CONTRACTS_DIR / 'frontend-api-contract.v1.openapi.json'

sys.path.insert(0, str(PROJECT_ROOT))

from brain.transcript_snapshot import build_contract_transcript_snapshots


def _load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))


def _body_ref_schema(body_ref: str, components: dict[str, Any]) -> dict[str, Any]:
    if body_ref == 'object':
        return {'type': 'object', 'additionalProperties': True}
    if body_ref in components:
        return {'$ref': f'#/components/schemas/{body_ref}'}
    return {
        'type': 'object',
        'additionalProperties': True,
        'description': f'Unresolved body_ref from contract: {body_ref}',
    }


def _parameter_schema(param: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {'type': param.get('type', 'string')}
    if 'enum' in param and param['enum']:
        schema['enum'] = list(param['enum'])
    if 'default' in param and param['default'] is not None:
        schema['default'] = param['default']
    if 'items' in param and param['items'] is not None:
        schema['items'] = deepcopy(param['items'])
    return schema


def build_openapi(contract: dict[str, Any]) -> dict[str, Any]:
    components = {
        **deepcopy(contract['components']['error_schemas']),
        **deepcopy(contract['components']['schemas']),
    }
    for name, payload in contract['components']['sse_schemas'].items():
        component_name = f"Sse{name[0].upper()}{name[1:]}Data"
        components[component_name] = deepcopy(payload['data'])

    paths: dict[str, Any] = {}
    for endpoint in contract['endpoints']:
        path_item = paths.setdefault(endpoint['path'], {})
        operation: dict[str, Any] = {
            'operationId': endpoint['id'].replace('.', '_'),
            'tags': [endpoint.get('group', 'default')],
            'summary': endpoint.get('summary', ''),
            'description': '\n'.join(endpoint.get('notes', [])) if endpoint.get('notes') else endpoint.get('summary', ''),
            'responses': {
                str(endpoint['success']['http_status']): {
                    'description': endpoint.get('summary', 'Success'),
                    'content': {
                        'application/json': {
                            'schema': _body_ref_schema(endpoint['success']['body_ref'], components),
                        }
                    },
                }
            },
            'x-runtime-required': bool(endpoint.get('runtime_required', False)),
            'x-frontend-preferred': bool(endpoint.get('frontend_preferred', False)),
            'x-latency': endpoint.get('latency', 'unknown'),
            'x-realtime': bool(endpoint.get('realtime', False)),
            'x-pagination': endpoint.get('pagination', 'none'),
        }
        parameters = []
        for param in endpoint.get('query_params', []):
            parameters.append({
                'name': param['name'],
                'in': 'query',
                'required': bool(param.get('required', False)),
                'schema': _parameter_schema(param),
                'description': param.get('description', ''),
            })
        for param in endpoint.get('path_params', []):
            parameters.append({
                'name': param['name'],
                'in': 'path',
                'required': True,
                'schema': _parameter_schema(param),
                'description': param.get('description', ''),
            })
        if parameters:
            operation['parameters'] = parameters
        if endpoint.get('request_body') is not None:
            operation['requestBody'] = {
                'required': True,
                'content': {
                    'application/json': {
                        'schema': deepcopy(endpoint['request_body']),
                    }
                },
            }
        for error in endpoint.get('errors', []):
            operation['responses'][str(error['http_status'])] = {
                'description': '; '.join(error.get('cases', [])) or 'Error response',
                'content': {
                    'application/json': {
                        'schema': _body_ref_schema(error['body_ref'], components),
                    }
                },
            }
        path_item[endpoint['method'].lower()] = operation

    sse_event_refs = list(contract['sse'].get('event_refs', []))
    paths[contract['sse']['path']] = {
        'get': {
            'operationId': 'events_stream',
            'tags': ['events'],
            'summary': 'Consume the runtime SSE stream for training and agent observability.',
            'description': 'Server-Sent Events endpoint used by the standalone frontend for cycle, agent, module log, and meeting speech updates.',
            'responses': {
                '200': {
                    'description': 'SSE stream established.',
                    'content': {
                        'text/event-stream': {
                            'schema': {'type': 'string'},
                        }
                    },
                }
            },
            'x-sse-event-refs': sse_event_refs,
            'x-sse-protocol': deepcopy(contract['sse'].get('protocol', {})),
            'x-runtime-required': False,
            'x-frontend-preferred': True,
            'x-realtime': True,
        }
    }

    return {
        'openapi': '3.1.0',
        'info': {
            'title': '投资进化系统 Frontend Contract',
            'version': contract['version'],
            'description': 'Derived OpenAPI document generated from docs/contracts/frontend-api-contract.v1.json',
        },
        'servers': [
            {'url': '/', 'description': 'Project root; paths remain absolute and unversioned.'},
        ],
        'paths': paths,
        'components': {
            'schemas': components,
        },
        'x-transcript-snapshots': deepcopy(contract.get('transcript_snapshots', {})),
    }


def build_contract_schema() -> dict[str, Any]:
    schema_fragment = {'type': 'object'}
    param_schema = {
        'type': 'object',
        'properties': {
            'name': {'type': 'string'},
            'type': {'type': 'string'},
            'required': {'type': 'boolean'},
            'default': {},
            'enum': {'type': 'array', 'items': {}},
            'description': {'type': 'string'},
            'items': {'type': 'object'},
        },
        'required': ['name', 'type'],
        'additionalProperties': True,
    }
    endpoint_schema = {
        'type': 'object',
        'properties': {
            'id': {'type': 'string'},
            'group': {'type': 'string'},
            'method': {'type': 'string'},
            'path': {'type': 'string'},
            'summary': {'type': 'string'},
            'runtime_required': {'type': 'boolean'},
            'frontend_preferred': {'type': 'boolean'},
            'replacement': {'type': ['string', 'null']},
            'query_params': {'type': 'array', 'items': param_schema},
            'path_params': {'type': 'array', 'items': param_schema},
            'request_body': {'type': ['object', 'null']},
            'success': {
                'type': 'object',
                'properties': {
                    'http_status': {'type': 'integer'},
                    'body_ref': {'type': 'string'},
                },
                'required': ['http_status', 'body_ref'],
                'additionalProperties': True,
            },
            'errors': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'http_status': {'type': 'integer'},
                        'body_ref': {'type': 'string'},
                        'cases': {'type': 'array', 'items': {'type': 'string'}},
                    },
                    'required': ['http_status', 'body_ref', 'cases'],
                    'additionalProperties': True,
                },
            },
            'latency': {'type': 'string'},
            'pagination': {'type': 'string'},
            'realtime': {'type': 'boolean'},
            'notes': {'type': 'array', 'items': {'type': 'string'}},
        },
        'required': [
            'id', 'group', 'method', 'path', 'summary', 'runtime_required', 'frontend_preferred',
            'replacement', 'query_params', 'path_params', 'request_body', 'success', 'errors',
            'latency', 'pagination', 'realtime', 'notes',
        ],
        'additionalProperties': False,
    }
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': 'https://contracts.local/frontend-api-contract.v1.schema.json',
        'title': 'Frontend API Contract V1',
        'type': 'object',
        'properties': {
            'contract_id': {'type': 'string', 'const': 'frontend-v1'},
            'version': {'type': 'string'},
            'published_at': {'type': 'string'},
            'api_base': {'type': 'string'},
            'frontend_shell_mount': {'type': 'string'},
            'legacy_shell_mount': {'type': 'string'},
            'contract_endpoint': {'type': 'string'},
            'goals': {'type': 'array', 'items': {'type': 'string'}},
            'compatibility': {'type': 'object', 'additionalProperties': {'type': ['string', 'boolean', 'number', 'null', 'object', 'array']}} ,
            'frontend_preferred_flows': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': {'type': 'string'}}},
            'components': {
                'type': 'object',
                'properties': {
                    'error_schemas': {'type': 'object', 'additionalProperties': schema_fragment},
                    'schemas': {'type': 'object', 'additionalProperties': schema_fragment},
                    'sse_schemas': {'type': 'object', 'additionalProperties': schema_fragment},
                },
                'required': ['error_schemas', 'schemas', 'sse_schemas'],
                'additionalProperties': False,
            },
            'sse': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'content_type': {'type': 'string'},
                    'protocol': {'type': 'object', 'additionalProperties': True},
                    'event_refs': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['path', 'content_type', 'protocol', 'event_refs'],
                'additionalProperties': False,
            },
            'endpoints': {'type': 'array', 'items': endpoint_schema},
            'transcript_snapshots': {
                'type': 'object',
                'properties': {
                    'schema_version': {'type': 'string'},
                    'examples': {'type': 'object', 'additionalProperties': {'type': 'object'}},
                },
                'required': ['schema_version', 'examples'],
                'additionalProperties': False,
            },
        },
        'required': [
            'contract_id', 'version', 'published_at', 'api_base', 'frontend_shell_mount', 'legacy_shell_mount',
            'contract_endpoint', 'goals', 'compatibility', 'frontend_preferred_flows', 'components', 'sse', 'endpoints', 'transcript_snapshots',
        ],
        'additionalProperties': False,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main() -> int:
    contract = _load_contract()
    contract['transcript_snapshots'] = build_contract_transcript_snapshots()
    write_json(CONTRACT_PATH, contract)
    write_json(SCHEMA_PATH, build_contract_schema())
    write_json(OPENAPI_PATH, build_openapi(contract))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
