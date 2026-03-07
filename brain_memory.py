"""Persistent memory store for commander runtime."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class MemoryRecord:
    id: str
    ts_ms: int
    kind: str
    session_key: str
    content: str
    metadata: dict[str, Any]


class MemoryStore:
    def __init__(self, path: Path, max_records: int = 10000):
        self.path = Path(path)
        self.max_records = max(100, int(max_records))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, kind: str, session_key: str, content: str, metadata: dict[str, Any] | None = None) -> MemoryRecord:
        rec = MemoryRecord(
            id=uuid.uuid4().hex[:12],
            ts_ms=int(time.time() * 1000),
            kind=str(kind),
            session_key=str(session_key),
            content=str(content),
            metadata=metadata or {},
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        self._truncate_if_needed()
        return rec

    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self._load_all()
        if kind:
            rows = [x for x in rows if x.get("kind") == kind]
        return rows[-max(1, int(limit)):]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = str(query or "").strip().lower()
        rows = self._load_all()
        if not q:
            return rows[-max(1, int(limit)):]
        hits = [r for r in rows if q in str(r.get("content", "")).lower()]
        return hits[-max(1, int(limit)):]

    def stats(self) -> dict[str, Any]:
        rows = self._load_all()
        return {
            "path": str(self.path),
            "records": len(rows),
            "max_records": self.max_records,
        }

    def _load_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except Exception:
                continue
        return rows

    def _truncate_if_needed(self) -> None:
        rows = self._load_all()
        if len(rows) <= self.max_records:
            return
        keep = rows[-self.max_records:]
        with self.path.open("w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
