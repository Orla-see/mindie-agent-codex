#!/usr/bin/env python3
"""Durable RSI-to-MindIE knowledge distillation queue."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "mindie-agent/rsi-distill-queue/v1"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def to_knowledge_request(request: Mapping[str, Any], *, domain: str | None = None) -> dict[str, Any]:
    """Convert the RSI request schema to the native knowledge_distill schema."""
    inputs = request.get("inputs")
    if not isinstance(inputs, list):
        raise ValueError("distill request requires inputs")
    value = {
        "inputs": [dict(item) for item in inputs if isinstance(item, Mapping)],
        "task_state": request.get("task_state", "verified_milestone"),
    }
    if request.get("proposal") is not None:
        value["proposal"] = dict(request["proposal"])
    if isinstance(request.get("related_refs"), list):
        value["related_refs"] = list(request["related_refs"])
    selected_domain = domain or request.get("domain")
    if selected_domain:
        value["domain"] = selected_domain
    return value


class DistillQueue:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            if value.get("schema") == SCHEMA:
                rows.append(value)
            elif value.get("schema") == "mindie-agent/rsi-distill-request/v1":
                request_json = _json(value)
                rows.append({
                    "schema": SCHEMA,
                    "queue_id": hashlib.sha256(request_json.encode()).hexdigest()[:24],
                    "request": value,
                    "status": "pending",
                    "attempts": 0,
                    "created_unix": time.time(),
                    "last_error": None,
                    "receipt": None,
                })
        return rows

    def enqueue(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_json = _json(request)
        queue_id = hashlib.sha256(request_json.encode()).hexdigest()[:24]
        rows = self._read()
        for row in rows:
            if row.get("queue_id") == queue_id:
                return row
        value = {
            "schema": SCHEMA,
            "queue_id": queue_id,
            "request": dict(request),
            "status": "pending",
            "attempts": 0,
            "created_unix": time.time(),
            "last_error": None,
            "receipt": None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
        return value

    def _rewrite(self, rows: list[dict[str, Any]]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text("".join(_json(row) + "\n" for row in rows), encoding="utf-8")
        temporary.replace(self.path)

    def pending(self) -> list[dict[str, Any]]:
        return [row for row in self._read() if row.get("status") in {"pending", "retryable"}]

    def flush(self, command: list[str] | None = None, *, cwd: str | None = None,
              timeout_s: int = 600, domain: str | None = None) -> list[dict[str, Any]]:
        """Submit pending items and persist every outcome before returning."""
        argv = command or [sys.executable, str(Path(__file__).with_name("knowledge_cli.py")),
                           "knowledge_distill"]
        rows = self._read()
        results = []
        for row in rows:
            if row.get("status") not in {"pending", "retryable"}:
                continue
            row["attempts"] = int(row.get("attempts", 0)) + 1
            request = to_knowledge_request(row["request"], domain=domain)
            try:
                process = subprocess.run(argv, input=_json(request) + "\n", text=True,
                                         capture_output=True, cwd=cwd, timeout=timeout_s,
                                         check=False)
                raw = (process.stdout or "").strip().splitlines()
                receipt = json.loads(raw[-1]) if raw else {}
                if not isinstance(receipt, Mapping):
                    receipt = {"stdout": process.stdout[-4000:]}
                structured = receipt.get("structuredContent") if isinstance(receipt, Mapping) else None
                uncertain = (receipt.get("receipt_unconfirmed") is True
                             or receipt.get("execution") == "outcome_unconfirmed"
                             or isinstance(structured, Mapping) and structured.get("receipt_unconfirmed") is True)
                if process.returncode == 0 and not receipt.get("isError"):
                    row["status"] = "submitted"
                    row["receipt"] = dict(receipt)
                    row["last_error"] = None
                elif uncertain:
                    row["status"] = "unknown"
                    row["last_error"] = "remote_outcome_unconfirmed_do_not_retry"
                    row["receipt"] = dict(receipt)
                else:
                    row["status"] = "retryable"
                    row["last_error"] = (receipt.get("error") or receipt.get("structuredContent")
                                          or process.stderr[-2000:] or "knowledge_distill_failed")
                    row["receipt"] = dict(receipt)
            except subprocess.TimeoutExpired:
                row["status"] = "unknown"
                row["last_error"] = "remote_timeout_outcome_unknown_do_not_retry"
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                row["status"] = "retryable"
                row["last_error"] = f"{type(exc).__name__}: {exc}"
            row["updated_unix"] = time.time()
            results.append(dict(row))
        if rows:
            self._rewrite(rows)
        return results


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue", type=Path)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--timeout-s", type=int, default=600)
    args = parser.parse_args(argv)
    result = DistillQueue(args.queue).flush(cwd=args.cwd, timeout_s=args.timeout_s,
                                            domain=args.domain)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(item.get("status") in {"submitted", "unknown"} for item in result) else 2


if __name__ == "__main__":
    raise SystemExit(main())
