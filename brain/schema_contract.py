from __future__ import annotations

from typing import Any

TASK_BUS_SCHEMA_VERSION = "task_bus.v2"
PLAN_SCHEMA_VERSION = "task_plan.v2"
COVERAGE_SCHEMA_VERSION = "task_coverage.v2"
ARTIFACT_TAXONOMY_SCHEMA_VERSION = "artifact_taxonomy.v2"
BOUNDED_WORKFLOW_SCHEMA_VERSION = "bounded_workflow.v2"

COVERAGE_KIND_PLAN_EXECUTION = "plan_vs_execution"
COVERAGE_KIND_WORKFLOW_PHASE = "workflow_phase_completion"

ARTIFACT_KIND_PATH = "path"
ARTIFACT_KIND_OBJECT = "object"
ARTIFACT_KIND_COLLECTION = "collection"
ARTIFACT_KIND_SCALAR = "scalar"
ARTIFACT_KIND_ID = "id"
ARTIFACT_KIND_UNKNOWN = "unknown"
ARTIFACT_KINDS = [
    ARTIFACT_KIND_COLLECTION,
    ARTIFACT_KIND_ID,
    ARTIFACT_KIND_OBJECT,
    ARTIFACT_KIND_PATH,
    ARTIFACT_KIND_SCALAR,
    ARTIFACT_KIND_UNKNOWN,
]

CONFIRMATION_STATE_PENDING = "pending_confirmation"
CONFIRMATION_STATE_CONFIRMED_OR_NOT_REQUIRED = "confirmed_or_not_required"
CONFIRMATION_STATE_NOT_APPLICABLE = "not_applicable"
CONFIRMATION_STATES = [
    CONFIRMATION_STATE_PENDING,
    CONFIRMATION_STATE_CONFIRMED_OR_NOT_REQUIRED,
    CONFIRMATION_STATE_NOT_APPLICABLE,
]

RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"
RISK_LEVELS = [RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH]

REASON_READ_ONLY_ANALYSIS = "read_only_analysis"
REASON_TOOL_GROUNDED_EXECUTION = "tool_grounded_execution"
REASON_STATE_CHANGING_REQUEST = "state_changing_request"
REASON_TRAINING_CHANGES_RUNTIME_STATE = "training_changes_runtime_state"
REASON_CODES = [
    REASON_READ_ONLY_ANALYSIS,
    REASON_STATE_CHANGING_REQUEST,
    REASON_TOOL_GROUNDED_EXECUTION,
    REASON_TRAINING_CHANGES_RUNTIME_STATE,
]
READONLY_DEFAULT_REASON_CODES = [REASON_READ_ONLY_ANALYSIS, REASON_TOOL_GROUNDED_EXECUTION]
MUTATING_DEFAULT_REASON_CODES = [REASON_STATE_CHANGING_REQUEST, REASON_TOOL_GROUNDED_EXECUTION]
TRAINING_DEFAULT_REASON_CODES = [REASON_TRAINING_CHANGES_RUNTIME_STATE, REASON_TOOL_GROUNDED_EXECUTION]

TASK_BUS_TOP_LEVEL_KEYS = ["schema_version", "planner", "gate", "audit"]
TASK_PLAN_KEYS = ["intent", "operation", "mode", "user_goal", "available_tools", "recommended_plan", "plan_summary"]
TASK_PLAN_SUMMARY_KEYS = ["schema_version", "available_tool_count", "recommended_step_count", "recommended_tool_count", "recommended_tools", "step_ids"]
TASK_GATE_KEYS = ["decision", "risk_level", "writes_state", "requires_confirmation", "reasons", "confirmation"]
TASK_CONFIRMATION_KEYS = ["required", "decision", "state", "reason_codes"]
TASK_AUDIT_KEYS = ["status", "started_at", "completed_at", "tool_count", "used_tools", "artifacts", "coverage", "artifact_taxonomy"]
TASK_COVERAGE_KEYS = [
    "schema_version",
    "coverage_kind",
    "recommended_step_count",
    "executed_step_count",
    "available_tool_count",
    "used_tool_count",
    "recommended_tool_count",
    "covered_recommended_tools",
    "covered_recommended_step_ids",
    "missing_planned_steps",
    "missing_planned_step_ids",
    "planned_step_coverage",
    "required_tool_coverage",
]
ARTIFACT_TAXONOMY_KEYS = ["schema_version", "count", "keys", "kinds", "path_keys", "object_keys", "collection_keys", "known_kinds"]
PLAN_STEP_KEYS = ["step_id", "tool", "args"]

BOUNDED_WORKFLOW_TOP_LEVEL_KEYS = ["entrypoint", "orchestration", "protocol", "artifacts", "coverage", "artifact_taxonomy"]
BOUNDED_PROTOCOL_KEYS = [
    "schema_version",
    "task_bus_schema_version",
    "plan_schema_version",
    "coverage_schema_version",
    "artifact_taxonomy_schema_version",
    "domain",
    "operation",
]
BOUNDED_COVERAGE_KEYS = [
    "schema_version",
    "coverage_kind",
    "workflow_step_count",
    "completed_workflow_step_count",
    "workflow_step_coverage",
    "phase_stat_key_count",
]


def task_bus_contract() -> dict[str, Any]:
    return {
        "schema_version": TASK_BUS_SCHEMA_VERSION,
        "top_level_keys": list(TASK_BUS_TOP_LEVEL_KEYS),
        "planner": {
            "keys": list(TASK_PLAN_KEYS),
            "step_required_keys": list(PLAN_STEP_KEYS),
            "summary_keys": list(TASK_PLAN_SUMMARY_KEYS),
            "summary_schema_version": PLAN_SCHEMA_VERSION,
        },
        "gate": {
            "keys": list(TASK_GATE_KEYS),
            "confirmation_keys": list(TASK_CONFIRMATION_KEYS),
            "confirmation_states": list(CONFIRMATION_STATES),
            "risk_levels": list(RISK_LEVELS),
            "reason_codes": list(REASON_CODES),
            "readonly_default_reason_codes": list(READONLY_DEFAULT_REASON_CODES),
            "mutating_default_reason_codes": list(MUTATING_DEFAULT_REASON_CODES),
            "training_default_reason_codes": list(TRAINING_DEFAULT_REASON_CODES),
        },
        "audit": {
            "keys": list(TASK_AUDIT_KEYS),
            "coverage_keys": list(TASK_COVERAGE_KEYS),
            "coverage_schema_version": COVERAGE_SCHEMA_VERSION,
            "coverage_kinds": [COVERAGE_KIND_PLAN_EXECUTION, COVERAGE_KIND_WORKFLOW_PHASE],
            "artifact_taxonomy_keys": list(ARTIFACT_TAXONOMY_KEYS),
            "artifact_taxonomy_schema_version": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
            "artifact_kinds": list(ARTIFACT_KINDS),
        },
    }


def bounded_workflow_contract() -> dict[str, Any]:
    return {
        "schema_version": BOUNDED_WORKFLOW_SCHEMA_VERSION,
        "top_level_keys": list(BOUNDED_WORKFLOW_TOP_LEVEL_KEYS),
        "protocol_keys": list(BOUNDED_PROTOCOL_KEYS),
        "protocol_versions": {
            "task_bus": TASK_BUS_SCHEMA_VERSION,
            "plan": PLAN_SCHEMA_VERSION,
            "coverage": COVERAGE_SCHEMA_VERSION,
            "artifact_taxonomy": ARTIFACT_TAXONOMY_SCHEMA_VERSION,
        },
        "coverage_keys": list(BOUNDED_COVERAGE_KEYS),
        "coverage_kind": COVERAGE_KIND_WORKFLOW_PHASE,
        "artifact_taxonomy_keys": list(ARTIFACT_TAXONOMY_KEYS),
        "artifact_kinds": list(ARTIFACT_KINDS),
    }


__all__ = [
    "ARTIFACT_KINDS",
    "ARTIFACT_KIND_COLLECTION",
    "ARTIFACT_KIND_ID",
    "ARTIFACT_KIND_OBJECT",
    "ARTIFACT_KIND_PATH",
    "ARTIFACT_KIND_SCALAR",
    "ARTIFACT_KIND_UNKNOWN",
    "ARTIFACT_TAXONOMY_KEYS",
    "ARTIFACT_TAXONOMY_SCHEMA_VERSION",
    "BOUNDED_COVERAGE_KEYS",
    "BOUNDED_PROTOCOL_KEYS",
    "BOUNDED_WORKFLOW_SCHEMA_VERSION",
    "BOUNDED_WORKFLOW_TOP_LEVEL_KEYS",
    "CONFIRMATION_STATES",
    "CONFIRMATION_STATE_CONFIRMED_OR_NOT_REQUIRED",
    "CONFIRMATION_STATE_NOT_APPLICABLE",
    "CONFIRMATION_STATE_PENDING",
    "MUTATING_DEFAULT_REASON_CODES",
    "READONLY_DEFAULT_REASON_CODES",
    "REASON_CODES",
    "REASON_READ_ONLY_ANALYSIS",
    "REASON_STATE_CHANGING_REQUEST",
    "REASON_TOOL_GROUNDED_EXECUTION",
    "REASON_TRAINING_CHANGES_RUNTIME_STATE",
    "RISK_LEVEL_HIGH",
    "RISK_LEVEL_LOW",
    "RISK_LEVEL_MEDIUM",
    "RISK_LEVELS",
    "TRAINING_DEFAULT_REASON_CODES",
    "COVERAGE_KIND_PLAN_EXECUTION",
    "COVERAGE_KIND_WORKFLOW_PHASE",
    "COVERAGE_SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "PLAN_STEP_KEYS",
    "TASK_AUDIT_KEYS",
    "TASK_BUS_SCHEMA_VERSION",
    "TASK_BUS_TOP_LEVEL_KEYS",
    "TASK_CONFIRMATION_KEYS",
    "TASK_COVERAGE_KEYS",
    "TASK_GATE_KEYS",
    "TASK_PLAN_KEYS",
    "TASK_PLAN_SUMMARY_KEYS",
    "bounded_workflow_contract",
    "task_bus_contract",
]
