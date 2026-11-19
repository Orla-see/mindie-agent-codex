#!/usr/bin/env python3
"""Persist replayable RSI evidence without coupling to a knowledge backend.

[Role] Stores mechanisms, requirements, correctness contracts, and measured
       evidence so later rounds can replay compatible findings.
[Invariants] Entries are append-only JSONL and never contain executable code.
[Timeouts & Locks] Uses O_APPEND plus fsync; no remote job is started.
[Side-Effect Policy] Only the explicitly supplied local ledger is modified.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "mindie-agent/rsi-memory/v1"
DISTILL_SCHEMA = "mindie-agent/rsi-distill-request/v1"


def _safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class MemoryStore:
    """A small append-only store suitable for a customer RSI state directory."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        value = {"schema": SCHEMA, "recorded_unix": time.time(), **_safe(dict(entry))}
        if not value.get("candidate_id") or not value.get("deployment_signature"):
            raise ValueError("memory entry requires candidate_id and deployment_signature")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        return value

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        result: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("schema") == SCHEMA:
                result.append(value)
        return result

    def find(self, deployment_signature: str, mechanism: str | None = None) -> list[dict[str, Any]]:
        result = [item for item in self.entries()
                  if item.get("deployment_signature") == deployment_signature]
        if mechanism is not None:
            result = [item for item in result if item.get("mechanism") == mechanism]
        return result

    def compact(self) -> None:
        """Drop malformed lines while preserving the append-only record order."""
        entries = self.entries()
        if not self.path.exists():
            return
        descriptor, filename = tempfile.mkstemp(prefix=self.path.name, dir=self.path.parent)
        os.close(descriptor)
        temporary = Path(filename)
        try:
            temporary.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True)
                                         + "\n" for item in entries), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def distill_request(entry: Mapping[str, Any], *, deployment_signature: str,
                    task_state: str = "verified_milestone") -> dict[str, Any]:
    """Build an auditable knowledge_distill payload from one RSI outcome."""
    if task_state not in {"verified_milestone", "completed"}:
        raise ValueError("distill task_state must be a verification milestone or completed")
    mechanism = str(entry.get("mechanism") or "candidate")
    outcome = str(entry.get("outcome") or "observed")
    verified = outcome in {"verified", "promoted", "supported"}
    if not verified:
        raise ValueError("only successful RSI outcomes can be distilled")
    evidence = entry.get("performance_evidence") or {}
    content = {
        "mechanism": mechanism,
        "mechanism_family": entry.get("mechanism_family") or "unknown",
        "outcome": outcome,
        "applicability": entry.get("applicability") or {},
        "requirements": entry.get("requirements") or {},
        "correctness_contract": entry.get("correctness_contract") or {},
        "performance_evidence": evidence,
        "limits": entry.get("limits") or [],
    }
    return {
        "schema": DISTILL_SCHEMA,
        "inputs": [{
            "kind": "reproduced_empirical",
            "outcome": "verified",
            "source_locator": f"rsi://{deployment_signature}/{mechanism}",
            "content": json.dumps(content, ensure_ascii=False, sort_keys=True),
            "evidence_refs": [str(item) for item in (entry.get("source_refs") or [])],
            "repetitions": int(entry.get("repetitions", 1) or 1),
        }],
        "task_state": task_state,
        "domain": "vllm-ascend",
        "proposal": {"mechanism": mechanism,
                      "mechanism_family": entry.get("mechanism_family") or "unknown",
                      "deployment_signature": deployment_signature},
    }
