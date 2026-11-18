#!/usr/bin/env python3
"""Install the host-side standard RSI entrypoint and binding template.

[Role] Makes the RSI action path stable during customer installation.
[Invariants] Existing entrypoints and binding files are preserved; generated
             files contain no customer shell command.
[Timeouts & Locks] Installation is a local atomic file operation; runtime
                    leases are acquired later by the RSI action entry.
[Side-Effect Policy] Creates only the standard wrapper and an absent binding
                     template below the selected RSI root.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


STANDARD_ENTRY = Path("bin/mindie_agent_rsi_entry.py")
DEFAULT_BINDINGS = Path("config/live-runtime-bindings.json")
_RELATIVE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,191}$")


def _atomic_write(path: Path, content: str, *, executable: bool = False) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    # ``Path.write_text`` did not accept ``newline`` on the Python 3.9
    # runtime shipped on some MindIE hosts.  The generated files contain
    # explicit LF text already, so the portable call is sufficient here.
    temporary.write_text(content, encoding="utf-8")
    if executable:
        temporary.chmod(temporary.stat().st_mode | 0o111)
    os.replace(temporary, path)
    return True


def _runtime_entry(root: Path, requested: str | None) -> str:
    candidates = [requested] if requested else [
        "rsi-mindie/bin/mindie_agent_rsi_entry.py",
        "bin/rsi_runtime_entry.py",
        "bin/runtime_entry.py",
    ]
    for value in candidates:
        if isinstance(value, str) and _RELATIVE.fullmatch(value) and ".." not in value.split("/"):
            if (root / value).is_file():
                return value
    return ""


def install(root: str | Path, *, bindings: str | Path | None = None,
            runtime_entry: str | None = None, template: str | None = None) -> dict[str, Any]:
    root_path = Path(root).resolve()
    entry_path = root_path / STANDARD_ENTRY
    source = Path(template) if template else Path(__file__).with_name("rsi_entry.py")
    if not source.is_file():
        raise FileNotFoundError(f"entry template not found: {source}")
    entry_created = _atomic_write(entry_path, source.read_text(encoding="utf-8"), executable=True)
    binding_path = Path(bindings) if bindings else root_path / DEFAULT_BINDINGS
    selected = _runtime_entry(root_path, runtime_entry)
    binding_created = False
    if not binding_path.exists():
        binding = {
            "schema": "mindie-agent/rsi-bindings/v1",
            "default": {
                "runtime_entry": selected,
                "runtime_python": "python3",
            },
        }
        binding_created = _atomic_write(
            binding_path,
            json.dumps(binding, ensure_ascii=False, indent=2) + "\n",
        )
    return {
        "rsi_root": str(root_path),
        "entry": str(entry_path),
        "entry_created": entry_created,
        "bindings": str(binding_path),
        "bindings_created": binding_created,
        "runtime_entry_detected": selected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rsi_root", type=Path)
    parser.add_argument("--bindings", type=Path, default=None)
    parser.add_argument("--runtime-entry", default=None)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(install(args.rsi_root, bindings=args.bindings,
                                 runtime_entry=args.runtime_entry), ensure_ascii=False,
                         sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
