from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping


@dataclass
class StockSummaryView(Mapping[str, Any]):
    code: str
    close: float | None = None
    change_5d: float | None = None
    change_20d: float | None = None
    ma_trend: str | None = None
    rsi: float | None = None
    macd: str | None = None
    bb_pos: float | None = None
    vol_ratio: float | None = None
    volatility: float | None = None
    algo_score: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | "StockSummaryView") -> "StockSummaryView":
        if isinstance(payload, StockSummaryView):
            return payload
        data = dict(payload or {})
        known = {
            "code",
            "close",
            "change_5d",
            "change_20d",
            "ma_trend",
            "rsi",
            "macd",
            "bb_pos",
            "vol_ratio",
            "volatility",
            "algo_score",
        }
        return cls(
            code=str(data.get("code") or ""),
            close=_coerce_float(data.get("close")),
            change_5d=_coerce_float(data.get("change_5d")),
            change_20d=_coerce_float(data.get("change_20d")),
            ma_trend=_coerce_text(data.get("ma_trend")),
            rsi=_coerce_float(data.get("rsi")),
            macd=_coerce_text(data.get("macd")),
            bb_pos=_coerce_float(data.get("bb_pos")),
            vol_ratio=_coerce_float(data.get("vol_ratio")),
            volatility=_coerce_float(data.get("volatility")),
            algo_score=_coerce_float(data.get("algo_score")),
            extras={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "close": self.close,
            "change_5d": self.change_5d,
            "change_20d": self.change_20d,
            "ma_trend": self.ma_trend,
            "rsi": self.rsi,
            "macd": self.macd,
            "bb_pos": self.bb_pos,
            "vol_ratio": self.vol_ratio,
            "volatility": self.volatility,
            "algo_score": self.algo_score,
        }
        return {key: value for key, value in {**payload, **self.extras}.items() if value is not None}

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__ = ["StockSummaryView"]
