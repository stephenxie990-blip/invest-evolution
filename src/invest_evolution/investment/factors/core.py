from __future__ import annotations

import re
from collections import Counter
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Factor contracts



FACTOR_REGISTRY_CONTRACT_VERSION = "factor_registry.v1"
FACTOR_AUDIT_CONTRACT_VERSION = "factor_audit_inventory.v1"
FACTOR_REGISTRY_SNAPSHOT_CONTRACT_VERSION = "factor_registry_snapshot.v1"
FACTOR_LIFECYCLE_CONTRACT_VERSION = "factor_lifecycle.v1"

FactorCategory = Literal[
    "raw_market_field",
    "derived_feature",
    "strategy_factor",
    "regime_factor",
    "risk_parameter",
    "evaluation_metric",
    "governance_metric",
]

FactorValueType = Literal["float", "int", "bool", "string"]
FactorStatus = Literal["draft", "candidate", "shadowed", "active", "deprecated", "rejected"]
FactorLifecycleAction = Literal[
    "submit_candidate",
    "start_shadow",
    "activate",
    "deprecate",
    "reject",
    "reopen_draft",
]

_FACTOR_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_DATE_PATTERN = re.compile(r"^\d{8}$")


class StrictFactorContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class FactorRecord(StrictFactorContractModel):
    factor_id: str
    name: str
    category: FactorCategory
    source_layer: str
    producers: list[str] = Field(default_factory=list)
    consumers: list[str] = Field(default_factory=list)
    calculation_definition: str
    unit_or_scale: str
    value_type: FactorValueType
    version: str = "v1"
    status: FactorStatus = "active"
    owner: str = "investment-system"
    depends_on: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("factor_id")
    @classmethod
    def _validate_factor_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not _FACTOR_ID_PATTERN.match(normalized):
            raise ValueError("factor_id must be snake_case")
        return normalized

    @field_validator("name", "source_layer", "calculation_definition", "unit_or_scale", "version", "owner", "notes")
    @classmethod
    def _strip_text_fields(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("producers", "consumers", "depends_on")
    @classmethod
    def _normalize_string_lists(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in list(value or []):
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized


class FactorAuditInventory(StrictFactorContractModel):
    contract_version: str = FACTOR_AUDIT_CONTRACT_VERSION
    registry_contract_version: str = FACTOR_REGISTRY_CONTRACT_VERSION
    generated_on: str
    scope: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    factors: list[FactorRecord] = Field(default_factory=list)

    @field_validator("generated_on")
    @classmethod
    def _validate_generated_on(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not _DATE_PATTERN.match(normalized):
            raise ValueError("generated_on must be YYYYMMDD")
        return normalized

    @field_validator("scope", "exclusions")
    @classmethod
    def _normalize_scope_lists(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in list(value or []):
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def _validate_factor_records(self) -> "FactorAuditInventory":
        factor_ids = [item.factor_id for item in self.factors]
        duplicates = [factor_id for factor_id, count in Counter(factor_ids).items() if count > 1]
        if duplicates:
            joined = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate factor_id detected: {joined}")
        return self

    def factor_map(self) -> dict[str, FactorRecord]:
        return {item.factor_id: item for item in self.factors}

    def category_counts(self) -> dict[str, int]:
        counts = Counter(item.category for item in self.factors)
        return {str(category): int(count) for category, count in sorted(counts.items())}


class FactorRegistrySnapshot(StrictFactorContractModel):
    contract_version: str = FACTOR_REGISTRY_SNAPSHOT_CONTRACT_VERSION
    registry_contract_version: str = FACTOR_REGISTRY_CONTRACT_VERSION
    source_inventory_contract_version: str = FACTOR_AUDIT_CONTRACT_VERSION
    generated_on: str
    entries: list[FactorRecord] = Field(default_factory=list)

    @field_validator("generated_on")
    @classmethod
    def _validate_generated_on(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not _DATE_PATTERN.match(normalized):
            raise ValueError("generated_on must be YYYYMMDD")
        return normalized

    @model_validator(mode="after")
    def _validate_entries(self) -> "FactorRegistrySnapshot":
        factor_ids = [item.factor_id for item in self.entries]
        duplicates = [factor_id for factor_id, count in Counter(factor_ids).items() if count > 1]
        if duplicates:
            joined = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate factor_id detected: {joined}")
        return self

    def factor_map(self) -> dict[str, FactorRecord]:
        return {item.factor_id: item for item in self.entries}

    def category_counts(self) -> dict[str, int]:
        counts = Counter(item.category for item in self.entries)
        return {str(category): int(count) for category, count in sorted(counts.items())}

    def status_counts(self) -> dict[str, int]:
        counts = Counter(item.status for item in self.entries)
        return {str(status): int(count) for status, count in sorted(counts.items())}


class FactorLifecycleEvent(StrictFactorContractModel):
    contract_version: str = FACTOR_LIFECYCLE_CONTRACT_VERSION
    factor_id: str
    action: FactorLifecycleAction
    from_status: FactorStatus
    to_status: FactorStatus
    effective_on: str
    actor: str
    reason_code: str
    note: str = ""

    @field_validator("effective_on")
    @classmethod
    def _validate_effective_on(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not _DATE_PATTERN.match(normalized):
            raise ValueError("effective_on must be YYYYMMDD")
        return normalized

    @field_validator("actor", "reason_code", "note")
    @classmethod
    def _strip_text_fields(cls, value: str) -> str:
        return str(value or "").strip()


# Factor lifecycle



_TRANSITIONS: Final[dict[FactorStatus, dict[FactorLifecycleAction, tuple[FactorStatus, str]]]] = {
    "draft": {
        "submit_candidate": ("candidate", "candidate_submitted"),
        "reject": ("rejected", "factor_rejected"),
    },
    "candidate": {
        "start_shadow": ("shadowed", "shadow_started"),
        "reject": ("rejected", "factor_rejected"),
        "reopen_draft": ("draft", "draft_reopened"),
    },
    "shadowed": {
        "activate": ("active", "factor_activated"),
        "reject": ("rejected", "factor_rejected"),
        "reopen_draft": ("draft", "draft_reopened"),
    },
    "active": {
        "deprecate": ("deprecated", "factor_deprecated"),
        "reject": ("rejected", "factor_rejected"),
    },
    "deprecated": {
        "reopen_draft": ("draft", "draft_reopened"),
        "reject": ("rejected", "factor_rejected"),
    },
    "rejected": {
        "reopen_draft": ("draft", "draft_reopened"),
    },
}


def build_lifecycle_transition_table() -> dict[str, dict[str, str]]:
    return {
        from_status: {
            action: to_status
            for action, (to_status, _reason_code) in action_map.items()
        }
        for from_status, action_map in _TRANSITIONS.items()
    }


def allowed_actions(status: FactorStatus) -> list[str]:
    return sorted(_TRANSITIONS.get(status, {}).keys())


def apply_lifecycle_action(
    record: FactorRecord,
    *,
    action: FactorLifecycleAction,
    effective_on: str,
    actor: str,
    note: str = "",
) -> tuple[FactorRecord, FactorLifecycleEvent]:
    transition = _TRANSITIONS.get(record.status, {}).get(action)
    if transition is None:
        raise ValueError(
            f"invalid lifecycle transition: status={record.status} action={action}"
        )

    to_status, reason_code = transition
    updated = record.model_copy(update={"status": to_status})
    event = FactorLifecycleEvent(
        factor_id=record.factor_id,
        action=action,
        from_status=record.status,
        to_status=to_status,
        effective_on=effective_on,
        actor=actor,
        reason_code=reason_code,
        note=note,
    )
    return updated, event


# Factor registry

def build_factor_registry_snapshot(
    inventory: FactorAuditInventory | None = None,
) -> FactorRegistrySnapshot:
    source = inventory or build_factor_audit_inventory()
    return FactorRegistrySnapshot(
        generated_on=source.generated_on,
        entries=[item.model_copy() for item in source.factors],
    )


def lookup_factor(
    snapshot: FactorRegistrySnapshot,
    factor_id: str,
) -> FactorRecord | None:
    return snapshot.factor_map().get(str(factor_id or "").strip())


def factors_for_consumer(
    snapshot: FactorRegistrySnapshot,
    consumer: str,
) -> list[FactorRecord]:
    target = str(consumer or "").strip()
    if not target:
        return []
    return [
        item
        for item in snapshot.entries
        if target in item.consumers
    ]


def factors_for_category(
    snapshot: FactorRegistrySnapshot,
    category: str,
) -> list[FactorRecord]:
    target = str(category or "").strip()
    if not target:
        return []
    return [
        item
        for item in snapshot.entries
        if item.category == target
    ]


# Factor audit



FACTOR_AUDIT_SEED_DATE = "20260318"

FACTOR_AUDIT_SCOPE = [
    "selection_features",
    "model_scoring_inputs",
    "regime_governance_inputs",
    "risk_execution_parameters",
    "review_evaluation_metrics",
    "governance_gate_metrics",
]

FACTOR_AUDIT_EXCLUSIONS = [
    "raw_ohlcv_columns_not_consumed_by_current_decision_path",
    "llm_prompt_text_and_freeform_reasoning",
    "ephemeral_logs_and_ui_only_payloads",
    "historical_outputs_not_referenced_by_active_runtime",
]


def _factor(
    factor_id: str,
    *,
    name: str,
    category: str,
    source_layer: str,
    producers: list[str],
    consumers: list[str],
    calculation_definition: str,
    unit_or_scale: str,
    value_type: str,
    depends_on: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "factor_id": factor_id,
        "name": name,
        "category": category,
        "source_layer": source_layer,
        "producers": list(producers),
        "consumers": list(consumers),
        "calculation_definition": calculation_definition,
        "unit_or_scale": unit_or_scale,
        "value_type": value_type,
        "depends_on": list(depends_on or []),
        "notes": notes,
    }


_SEED_FACTORS: tuple[dict[str, Any], ...] = (
    _factor(
        "close_price",
        name="Latest Close Price",
        category="raw_market_field",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot.build_batch_indicator_snapshot"],
        consumers=[
            "invest.foundation.compute.batch_snapshot._resolve_ma_trend",
            "invest.governance.engine.MarketObservationService",
        ],
        calculation_definition="Latest normalized close price before cutoff_date.",
        unit_or_scale="price",
        value_type="float",
        notes="Retained because trend and regime computations derive directly from latest close.",
    ),
    _factor(
        "change_5d_pct",
        name="5 Day Change",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot._pct_change"],
        consumers=[
            "invest.foundation.compute.factors.calc_algo_score",
            "invest.runtimes.scorers.MeanReversionScorer",
            "invest.runtimes.scorers.DefensiveLowVolScorer",
            "invest.agents.hunters.ContrarianAgent",
        ],
        calculation_definition="Percent change between latest close and close 5 sessions earlier.",
        unit_or_scale="pct",
        value_type="float",
        notes="Shared short-horizon direction input across summary scoring and strategy scorers.",
    ),
    _factor(
        "change_20d_pct",
        name="20 Day Change",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot._pct_change"],
        consumers=[
            "invest.foundation.compute.factors.calc_algo_score",
            "invest.runtimes.scorers.DefensiveLowVolScorer",
            "invest.runtimes.scorers.ValueQualityScorer",
            "invest.foundation.compute.market_stats.compute_market_stats",
        ],
        calculation_definition="Percent change between latest close and close 20 sessions earlier.",
        unit_or_scale="pct",
        value_type="float",
        notes="Also feeds market-wide regime statistics through averaged 20d return.",
    ),
    _factor(
        "sma_5",
        name="5 Day Moving Average",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot.build_batch_indicator_snapshot"],
        consumers=["invest.foundation.compute.batch_snapshot._resolve_ma_trend"],
        calculation_definition="Simple moving average over the latest 5 closes.",
        unit_or_scale="price",
        value_type="float",
    ),
    _factor(
        "sma_20",
        name="20 Day Moving Average",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot.build_batch_indicator_snapshot"],
        consumers=[
            "invest.foundation.compute.batch_snapshot._resolve_ma_trend",
            "invest.foundation.compute.market_stats.compute_market_stats",
        ],
        calculation_definition="Simple moving average over the latest 20 closes.",
        unit_or_scale="price",
        value_type="float",
    ),
    _factor(
        "ma_trend_label",
        name="Moving Average Trend Label",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot._resolve_ma_trend"],
        consumers=[
            "invest.foundation.compute.factors.calc_algo_score",
            "invest.runtimes.scorers.DefensiveLowVolScorer",
            "invest.agents.hunters.TrendHunterAgent",
            "invest.agents.specialists.DefensiveAgent",
        ],
        calculation_definition="Discrete label derived from latest close versus SMA5/SMA20 relationship: 多头/空头/交叉.",
        unit_or_scale="enum_label",
        value_type="string",
    ),
    _factor(
        "rsi_14",
        name="RSI 14",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot.build_batch_indicator_snapshot"],
        consumers=[
            "invest.foundation.compute.factors.calc_algo_score",
            "invest.runtimes.scorers.MeanReversionScorer",
            "invest.runtimes.scorers.DefensiveLowVolScorer",
            "invest.runtimes.scorers.ValueQualityScorer",
            "invest.agents.hunters.TrendHunterAgent",
            "invest.agents.hunters.ContrarianAgent",
        ],
        calculation_definition="RSI value sourced from indicator snapshot using 14 period configuration.",
        unit_or_scale="oscillator_0_100",
        value_type="float",
    ),
    _factor(
        "macd_cross_label",
        name="MACD Cross Label",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot._macd_cross_to_legacy_label"],
        consumers=[
            "invest.foundation.compute.factors.calc_algo_score",
            "invest.agents.hunters.TrendHunterAgent",
            "invest.runtimes.scorers.MomentumScorer",
        ],
        calculation_definition="Legacy MACD cross label normalized to 金叉/看多/中性/看空/死叉.",
        unit_or_scale="enum_label",
        value_type="string",
    ),
    _factor(
        "bb_position_20",
        name="Bollinger Position 20",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot.build_batch_indicator_snapshot"],
        consumers=[
            "invest.foundation.compute.factors.calc_algo_score",
            "invest.runtimes.scorers.MeanReversionScorer",
            "invest.runtimes.scorers.DefensiveLowVolScorer",
            "invest.runtimes.scorers.ValueQualityScorer",
            "invest.agents.hunters.ContrarianAgent",
        ],
        calculation_definition="Normalized position of latest close inside 20 period Bollinger band snapshot.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "volume_ratio_5_20",
        name="Volume Ratio 5/20",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot.build_batch_indicator_snapshot"],
        consumers=[
            "invest.runtimes.scorers.MeanReversionScorer",
            "invest.runtimes.scorers.DefensiveLowVolScorer",
            "invest.runtimes.mean_reversion.MeanReversionRuntime",
            "invest.runtimes.defensive_low_vol.DefensiveLowVolRuntime",
        ],
        calculation_definition="Short-vs-medium volume ratio derived from indicator snapshot.",
        unit_or_scale="ratio",
        value_type="float",
    ),
    _factor(
        "realized_volatility_20d",
        name="Realized Volatility 20D",
        category="derived_feature",
        source_layer="invest.foundation.compute.batch_snapshot",
        producers=["invest.foundation.compute.batch_snapshot._volatility"],
        consumers=[
            "invest.foundation.compute.market_stats.compute_market_stats",
            "invest.runtimes.scorers.DefensiveLowVolScorer",
            "invest.runtimes.scorers.ValueQualityScorer",
            "invest.agents.specialists.DefensiveAgent",
        ],
        calculation_definition="Standard deviation of daily returns over the latest 20 valid sessions.",
        unit_or_scale="std_ratio",
        value_type="float",
    ),
    _factor(
        "algo_score",
        name="Summary Algo Score",
        category="strategy_factor",
        source_layer="invest.foundation.compute.factors",
        producers=["invest.foundation.compute.factors.calc_algo_score"],
        consumers=[
            "invest.foundation.compute.batch_snapshot.build_batch_summary",
            "invest.runtimes.momentum.MomentumRuntime",
            "invest.agents.hunters.TrendHunterAgent",
            "invest.agents.hunters.ContrarianAgent",
        ],
        calculation_definition="Weighted composite score over change_5d/change_20d/MA trend/RSI/MACD/Bollinger inputs.",
        unit_or_scale="score_unbounded",
        value_type="float",
        depends_on=[
            "change_5d_pct",
            "change_20d_pct",
            "ma_trend_label",
            "rsi_14",
            "macd_cross_label",
            "bb_position_20",
        ],
    ),
    _factor(
        "trend_score",
        name="Trend Hunter Score",
        category="strategy_factor",
        source_layer="invest.agents.hunters",
        producers=["invest.agents.hunters.TrendHunterAgent._fallback_analysis"],
        consumers=["invest_evolution.application.training.execution.TrainingSelectionService"],
        calculation_definition="Fallback trend-selection score derived from MA trend, MACD state and RSI band preference.",
        unit_or_scale="score_0_1",
        value_type="float",
        depends_on=["algo_score", "ma_trend_label", "macd_cross_label", "rsi_14"],
    ),
    _factor(
        "contrarian_score",
        name="Contrarian Score",
        category="strategy_factor",
        source_layer="invest.agents.hunters",
        producers=["invest.agents.hunters.ContrarianAgent._fallback_analysis"],
        consumers=["invest_evolution.application.training.execution.TrainingSelectionService"],
        calculation_definition="Fallback contrarian score derived from oversold RSI, Bollinger position and recent drawdown.",
        unit_or_scale="score_0_1",
        value_type="float",
        depends_on=["change_5d_pct", "rsi_14", "bb_position_20", "algo_score"],
    ),
    _factor(
        "reversion_score",
        name="Mean Reversion Score",
        category="strategy_factor",
        source_layer="invest.runtimes.scorers",
        producers=["invest.runtimes.mean_reversion.MeanReversionRuntime", "invest.runtimes.scorers.MeanReversionScorer"],
        consumers=[
            "invest.runtimes.mean_reversion.MeanReversionRuntime",
            "invest_evolution.application.training.execution.TrainingSelectionService",
            "invest.agents.reviewers.ReviewDecisionAgent",
        ],
        calculation_definition="Rule-based score over oversold RSI, Bollinger lower-band distance, 5d/20d drop, volume ratio and trend penalties.",
        unit_or_scale="score_unbounded",
        value_type="float",
        depends_on=[
            "change_5d_pct",
            "change_20d_pct",
            "rsi_14",
            "bb_position_20",
            "volume_ratio_5_20",
        ],
    ),
    _factor(
        "defensive_score",
        name="Defensive Low Vol Score",
        category="strategy_factor",
        source_layer="invest.runtimes.scorers",
        producers=["invest.runtimes.defensive_low_vol.DefensiveLowVolRuntime", "invest.runtimes.scorers.DefensiveLowVolScorer"],
        consumers=[
            "invest.runtimes.defensive_low_vol.DefensiveLowVolRuntime",
            "invest_evolution.application.training.execution.TrainingSelectionService",
            "invest.agents.specialists.DefensiveAgent",
        ],
        calculation_definition="Rule-based score over low volatility preference, RSI comfort band, 20d stability, trend confirmation and Bollinger band placement.",
        unit_or_scale="score_unbounded",
        value_type="float",
        depends_on=[
            "realized_volatility_20d",
            "rsi_14",
            "change_20d_pct",
            "change_5d_pct",
            "ma_trend_label",
            "bb_position_20",
            "volume_ratio_5_20",
        ],
    ),
    _factor(
        "value_quality_score",
        name="Value Quality Score",
        category="strategy_factor",
        source_layer="invest.runtimes.scorers",
        producers=["invest.runtimes.value_quality.ValueQualityRuntime", "invest.runtimes.scorers.ValueQualityScorer"],
        consumers=[
            "invest.runtimes.value_quality.ValueQualityRuntime",
            "invest_evolution.application.training.execution.TrainingSelectionService",
            "invest.agents.specialists.QualityAgent",
        ],
        calculation_definition="Rule-based score over PE/PB/ROE/market cap plus RSI, 20d change and low-volatility preference.",
        unit_or_scale="score_unbounded",
        value_type="float",
        depends_on=[
            "pe_ttm",
            "pb_ratio",
            "roe_pct",
            "market_cap",
            "rsi_14",
            "change_20d_pct",
            "realized_volatility_20d",
        ],
    ),
    _factor(
        "pe_ttm",
        name="PE TTM",
        category="raw_market_field",
        source_layer="invest.runtimes.value_quality",
        producers=["invest.runtimes.value_quality.ValueQualityRuntime._fundamental_snapshot"],
        consumers=[
            "invest.runtimes.scorers.ValueQualityScorer",
            "invest.agents.specialists.QualityAgent",
        ],
        calculation_definition="Latest trailing PE value extracted from stock dataframe.",
        unit_or_scale="ratio",
        value_type="float",
    ),
    _factor(
        "pb_ratio",
        name="PB Ratio",
        category="raw_market_field",
        source_layer="invest.runtimes.value_quality",
        producers=["invest.runtimes.value_quality.ValueQualityRuntime._fundamental_snapshot"],
        consumers=[
            "invest.runtimes.scorers.ValueQualityScorer",
            "invest.agents.specialists.QualityAgent",
        ],
        calculation_definition="Latest price-to-book ratio extracted from stock dataframe.",
        unit_or_scale="ratio",
        value_type="float",
    ),
    _factor(
        "roe_pct",
        name="ROE Percent",
        category="raw_market_field",
        source_layer="invest.runtimes.value_quality",
        producers=["invest.runtimes.value_quality.ValueQualityRuntime._fundamental_snapshot"],
        consumers=[
            "invest.runtimes.scorers.ValueQualityScorer",
            "invest.agents.specialists.QualityAgent",
        ],
        calculation_definition="Latest return-on-equity percentage extracted from stock dataframe.",
        unit_or_scale="pct",
        value_type="float",
    ),
    _factor(
        "market_cap",
        name="Market Cap",
        category="raw_market_field",
        source_layer="invest.runtimes.value_quality",
        producers=["invest.runtimes.value_quality.ValueQualityRuntime._fundamental_snapshot"],
        consumers=[
            "invest.runtimes.scorers.ValueQualityScorer",
            "invest.runtimes.value_quality.ValueQualityRuntime",
        ],
        calculation_definition="Latest market capitalization extracted from stock dataframe.",
        unit_or_scale="currency",
        value_type="float",
    ),
    _factor(
        "market_breadth",
        name="Market Breadth",
        category="regime_factor",
        source_layer="invest.foundation.compute.market_stats",
        producers=["invest.foundation.compute.market_stats.compute_market_stats"],
        consumers=[
            "invest.governance.engine.RegimeClassifier",
            "invest.runtimes.momentum.MomentumRuntime",
            "invest.runtimes.value_quality.ValueQualityRuntime",
            "invest.runtimes.defensive_low_vol.DefensiveLowVolRuntime",
            "invest.runtimes.mean_reversion.MeanReversionRuntime",
        ],
        calculation_definition="Share of valid stocks with positive 5d change inside the observed universe.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "avg_change_20d_pct",
        name="Average 20 Day Change",
        category="regime_factor",
        source_layer="invest.foundation.compute.market_stats",
        producers=["invest.foundation.compute.market_stats.compute_market_stats"],
        consumers=[
            "invest.governance.engine.RegimeClassifier",
            "invest.runtimes.mean_reversion.MeanReversionRuntime",
            "invest.runtimes.context_renderer.render_market_narrative",
        ],
        calculation_definition="Average 20 day percent change across valid stock universe at cutoff_date.",
        unit_or_scale="pct",
        value_type="float",
    ),
    _factor(
        "above_ma20_ratio",
        name="Above MA20 Ratio",
        category="regime_factor",
        source_layer="invest.foundation.compute.market_stats",
        producers=["invest.foundation.compute.market_stats.compute_market_stats"],
        consumers=[
            "invest.governance.engine.RegimeClassifier",
            "invest.runtimes.momentum.MomentumRuntime",
            "invest.runtimes.defensive_low_vol.DefensiveLowVolRuntime",
        ],
        calculation_definition="Share of valid stocks whose latest close remains above the 20 day moving average.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "avg_market_volatility",
        name="Average Market Volatility",
        category="regime_factor",
        source_layer="invest.foundation.compute.market_stats",
        producers=["invest.foundation.compute.market_stats.compute_market_stats"],
        consumers=[
            "invest.governance.engine.RegimeClassifier",
            "invest.runtimes.momentum.MomentumRuntime",
            "invest.runtimes.value_quality.ValueQualityRuntime",
            "invest.runtimes.defensive_low_vol.DefensiveLowVolRuntime",
            "invest.runtimes.mean_reversion.MeanReversionRuntime",
        ],
        calculation_definition="Average realized volatility across valid stock universe.",
        unit_or_scale="std_ratio",
        value_type="float",
    ),
    _factor(
        "index_change_20d_pct",
        name="Index 20 Day Change",
        category="regime_factor",
        source_layer="invest.governance.engine",
        producers=["invest.governance.engine.MarketObservationService._summarize_index_frame"],
        consumers=["invest.governance.engine.RegimeClassifier"],
        calculation_definition="20 day percent change of the configured market index used as routing fallback evidence.",
        unit_or_scale="pct",
        value_type="float",
        notes="Only present when market index frame is available from the data manager.",
    ),
    _factor(
        "regime_label",
        name="Regime Label",
        category="regime_factor",
        source_layer="invest.governance.engine",
        producers=["invest.governance.engine.RegimeClassifier"],
        consumers=[
            "invest.runtimes.base.ManagerRuntime",
            "invest_evolution.application.training.execution.TrainingSelectionService",
            "invest_evolution.application.training.review.TrainingReviewStageService",
            "invest_evolution.application.training.research",
        ],
        calculation_definition="Discrete regime classification resolved as bull / oscillation / bear.",
        unit_or_scale="enum_label",
        value_type="string",
        depends_on=[
            "avg_change_20d_pct",
            "above_ma20_ratio",
            "avg_market_volatility",
            "market_breadth",
            "index_change_20d_pct",
        ],
    ),
    _factor(
        "regime_confidence",
        name="Regime Confidence",
        category="regime_factor",
        source_layer="invest.governance.engine",
        producers=["invest.governance.engine.RegimeClassifier"],
        consumers=[
            "invest.governance.engine.GovernanceCoordinator",
            "invest_evolution.application.training.execution.TrainingSelectionService",
            "invest.contracts.AgentContext",
        ],
        calculation_definition="Confidence score attached to regime classification or routing override consensus.",
        unit_or_scale="ratio_0_1",
        value_type="float",
        depends_on=["regime_label", "avg_change_20d_pct", "above_ma20_ratio", "market_breadth"],
    ),
    _factor(
        "suggested_exposure",
        name="Suggested Exposure",
        category="regime_factor",
        source_layer="invest.governance.engine",
        producers=["invest.governance.engine.RegimeClassifier"],
        consumers=[
            "invest_evolution.application.training.execution.TrainingSelectionService",
            "invest.contracts.SignalPacket",
        ],
        calculation_definition="Regime-specific portfolio exposure hint derived from routing classifier output.",
        unit_or_scale="ratio_0_1",
        value_type="float",
        depends_on=["regime_label"],
    ),
    _factor(
        "stop_loss_pct",
        name="Stop Loss Percent",
        category="risk_parameter",
        source_layer="invest.runtimes.configs",
        producers=["invest.runtimes.configs.*", "invest.runtimes.defaults.COMMON_RISK_DEFAULTS"],
        consumers=[
            "invest.foundation.risk",
            "invest.contracts.StockSignal",
            "invest.agents.reviewers.EvoJudgeAgent",
        ],
        calculation_definition="Configured stop loss percentage applied to signals, trading plans and review-stage parameter adjustments.",
        unit_or_scale="pct_ratio",
        value_type="float",
        notes="Audited as a causal execution parameter, not as a market-derived feature.",
    ),
    _factor(
        "take_profit_pct",
        name="Take Profit Percent",
        category="risk_parameter",
        source_layer="invest.runtimes.configs",
        producers=["invest.runtimes.configs.*", "invest.runtimes.defaults.COMMON_RISK_DEFAULTS"],
        consumers=[
            "invest.foundation.risk",
            "invest.contracts.StockSignal",
            "invest.agents.reviewers.EvoJudgeAgent",
        ],
        calculation_definition="Configured take-profit percentage attached to signals and risk-control decisions.",
        unit_or_scale="pct_ratio",
        value_type="float",
    ),
    _factor(
        "trailing_stop_pct",
        name="Trailing Stop Percent",
        category="risk_parameter",
        source_layer="invest.runtimes.configs",
        producers=["invest.runtimes.configs.*", "invest.runtimes.defaults.COMMON_RISK_DEFAULTS"],
        consumers=[
            "invest.foundation.risk",
            "invest.contracts.StockSignal",
            "invest_evolution.application.training.review.TrainingReviewStageService",
        ],
        calculation_definition="Configured trailing stop percentage used by execution and review-stage clamp logic.",
        unit_or_scale="pct_ratio",
        value_type="float",
    ),
    _factor(
        "position_size_pct",
        name="Position Size Percent",
        category="risk_parameter",
        source_layer="invest.runtimes.configs",
        producers=["invest.runtimes.configs.*", "invest.runtimes.defaults.COMMON_PARAM_DEFAULTS"],
        consumers=[
            "invest.foundation.risk",
            "invest.shared.contracts.PositionPlan",
            "invest.agents.reviewers.EvoJudgeAgent",
        ],
        calculation_definition="Configured per-position sizing ratio after runtime sanitization and review adjustments.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "cash_reserve_pct",
        name="Cash Reserve Percent",
        category="risk_parameter",
        source_layer="invest.runtimes.configs",
        producers=["invest.runtimes.configs.*", "invest.runtimes.defaults.COMMON_PARAM_DEFAULTS"],
        consumers=[
            "invest.contracts.SignalPacket",
            "invest_evolution.application.training.execution.TrainingSelectionService",
            "invest_evolution.application.training.review.TrainingReviewStageService",
        ],
        calculation_definition="Configured portfolio cash reserve ratio applied at signal packet and review stages.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "max_positions",
        name="Max Positions",
        category="risk_parameter",
        source_layer="invest.runtimes.configs",
        producers=["invest.runtimes.configs.*", "invest.runtimes.defaults.COMMON_PARAM_DEFAULTS"],
        consumers=[
            "invest.contracts.SignalPacket",
            "invest_evolution.application.training.execution.TrainingSelectionService",
            "invest.foundation.engine",
        ],
        calculation_definition="Configured cap on concurrently held positions in the portfolio.",
        unit_or_scale="count",
        value_type="int",
    ),
    _factor(
        "benchmark_passed",
        name="Benchmark Passed",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics",
        producers=["invest.foundation.metrics.benchmark.BenchmarkEvaluator"],
        consumers=[
            "invest_evolution.application.training.research",
            "invest_evolution.application.training.research",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Boolean outcome indicating whether benchmark criteria checks all passed for the evaluated cycle.",
        unit_or_scale="bool",
        value_type="bool",
    ),
    _factor(
        "excess_return_pct",
        name="Excess Return Percent",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics",
        producers=["invest.foundation.metrics.benchmark.BenchmarkEvaluator"],
        consumers=[
            "invest_evolution.application.training.research",
            "invest_evolution.application.training.observability",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Strategy total return minus benchmark total return in percentage points.",
        unit_or_scale="pct",
        value_type="float",
    ),
    _factor(
        "sharpe_ratio",
        name="Sharpe Ratio",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics",
        producers=["invest.foundation.metrics.benchmark.BenchmarkEvaluator"],
        consumers=[
            "invest_evolution.application.training.observability.evaluate_freeze_gate",
            "invest_evolution.application.training.research",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Annualized Sharpe ratio of the cycle equity curve against configured risk-free rate.",
        unit_or_scale="ratio",
        value_type="float",
    ),
    _factor(
        "calmar_ratio",
        name="Calmar Ratio",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics",
        producers=["invest.foundation.metrics.benchmark.BenchmarkEvaluator"],
        consumers=[
            "invest_evolution.application.training.observability",
            "invest.agents.reviewers.StrategistAgent",
        ],
        calculation_definition="Annualized return divided by maximum drawdown for the evaluated cycle.",
        unit_or_scale="ratio",
        value_type="float",
    ),
    _factor(
        "max_drawdown_pct",
        name="Max Drawdown Percent",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics",
        producers=["invest.foundation.metrics.benchmark.BenchmarkEvaluator"],
        consumers=[
            "invest_evolution.application.training.observability.evaluate_freeze_gate",
            "invest_evolution.application.training.research",
            "invest.agents.reviewers.StrategistAgent",
        ],
        calculation_definition="Maximum peak-to-trough drawdown percentage over the evaluated cycle.",
        unit_or_scale="pct",
        value_type="float",
    ),
    _factor(
        "win_rate",
        name="Win Rate",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics",
        producers=[
            "invest.foundation.metrics.benchmark.BenchmarkEvaluator",
            "invest.foundation.metrics.cycle.StrategyEvaluator",
        ],
        consumers=[
            "invest_evolution.application.training.research",
            "invest.agents.reviewers.StrategistAgent",
            "invest.agents.reviewers.EvoJudgeAgent",
        ],
        calculation_definition="Share of winning sell trades inside benchmark evaluator or strategy analysis payloads.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "profit_loss_ratio",
        name="Profit Loss Ratio",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics",
        producers=["invest.foundation.metrics.benchmark.BenchmarkEvaluator"],
        consumers=[
            "invest_evolution.application.training.observability",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Average win magnitude divided by average loss magnitude for completed sell trades.",
        unit_or_scale="ratio",
        value_type="float",
    ),
    _factor(
        "monthly_turnover",
        name="Monthly Turnover",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics",
        producers=["invest.foundation.metrics.benchmark.BenchmarkEvaluator"],
        consumers=[
            "invest_evolution.application.training.observability",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Normalized monthly turnover derived from trade count over evaluation horizon.",
        unit_or_scale="ratio",
        value_type="float",
    ),
    _factor(
        "signal_accuracy",
        name="Signal Accuracy",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics.cycle",
        producers=["invest.foundation.metrics.cycle.StrategyEvaluator"],
        consumers=[
            "invest_evolution.application.training.research",
            "invest.agents.reviewers.StrategistAgent",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Winning-trade share proxy produced by StrategyEvaluator for per-cycle scoring.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "timing_score",
        name="Timing Score",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics.cycle",
        producers=["invest.foundation.metrics.cycle.StrategyEvaluator"],
        consumers=[
            "invest_evolution.application.training.observability",
            "invest.agents.reviewers.StrategistAgent",
        ],
        calculation_definition="One minus normalized max drawdown over daily records, used as timing-quality proxy.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "risk_control_score",
        name="Risk Control Score",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics.cycle",
        producers=["invest.foundation.metrics.cycle.StrategyEvaluator"],
        consumers=[
            "invest_evolution.application.training.observability",
            "invest.agents.reviewers.StrategistAgent",
            "invest.agents.reviewers.EvoJudgeAgent",
        ],
        calculation_definition="Proxy score based on stop-loss / take-profit execution frequency plus configured base score.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
    _factor(
        "overall_strategy_score",
        name="Overall Strategy Score",
        category="evaluation_metric",
        source_layer="invest.foundation.metrics.cycle",
        producers=["invest.foundation.metrics.cycle.StrategyEvaluator"],
        consumers=[
            "invest_evolution.application.training.research",
            "invest_evolution.application.training.observability",
            "invest.agents.reviewers.ReviewDecisionAgent",
        ],
        calculation_definition="Weighted aggregate over signal_accuracy, timing_score and risk_control_score.",
        unit_or_scale="ratio_0_1",
        value_type="float",
        depends_on=["signal_accuracy", "timing_score", "risk_control_score"],
    ),
    _factor(
        "candidate_pending_count",
        name="Candidate Pending Count",
        category="governance_metric",
        source_layer="invest_evolution.application.training.observability",
        producers=["invest_evolution.application.training.observability.build_freeze_report"],
        consumers=[
            "invest_evolution.application.training.observability.evaluate_freeze_gate",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Count of candidate records still pending governance completion.",
        unit_or_scale="count",
        value_type="int",
    ),
    _factor(
        "override_pending_count",
        name="Override Pending Count",
        category="governance_metric",
        source_layer="invest_evolution.application.training.observability",
        producers=["invest_evolution.application.training.observability.build_freeze_report"],
        consumers=[
            "invest_evolution.application.training.observability.evaluate_freeze_gate",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Count of governance override records that remain unresolved.",
        unit_or_scale="count",
        value_type="int",
    ),
    _factor(
        "active_candidate_drift_rate",
        name="Active Candidate Drift Rate",
        category="governance_metric",
        source_layer="invest_evolution.application.training.observability",
        producers=["invest_evolution.application.training.observability.build_freeze_report"],
        consumers=[
            "invest_evolution.application.training.observability.evaluate_freeze_gate",
            "invest_evolution.application.training.observability",
        ],
        calculation_definition="Rate of active records drifting away from the currently tracked candidate lineage.",
        unit_or_scale="ratio_0_1",
        value_type="float",
    ),
)


def build_factor_audit_inventory() -> FactorAuditInventory:
    return FactorAuditInventory(
        generated_on=FACTOR_AUDIT_SEED_DATE,
        scope=FACTOR_AUDIT_SCOPE,
        exclusions=FACTOR_AUDIT_EXCLUSIONS,
        factors=[FactorRecord(**payload) for payload in _SEED_FACTORS],
    )


def render_factor_audit_inventory_markdown(
    inventory: FactorAuditInventory | None = None,
) -> str:
    payload = inventory or build_factor_audit_inventory()
    lines = [
        "# P1 Factor Audit Inventory",
        "",
        f"- generated_on: `{payload.generated_on}`",
        f"- contract_version: `{payload.contract_version}`",
        f"- registry_contract_version: `{payload.registry_contract_version}`",
        f"- factor_count: `{len(payload.factors)}`",
        "",
        "## Scope",
        "",
    ]
    lines.extend(f"- {item}" for item in payload.scope)
    lines.extend(["", "## Exclusions", ""])
    lines.extend(f"- {item}" for item in payload.exclusions)
    lines.extend(["", "## Category Summary", ""])
    for category, count in payload.category_counts().items():
        lines.append(f"- `{category}`: {count}")
    lines.extend(["", "## Seed Factors", "", "| factor_id | category | source_layer | unit |", "|---|---|---|---|"])
    for item in payload.factors:
        lines.append(
            f"| `{item.factor_id}` | `{item.category}` | `{item.source_layer}` | `{item.unit_or_scale}` |"
        )
    return "\n".join(lines)

__all__ = [name for name in globals() if not name.startswith('_')]
