#!/usr/bin/env python3
"""Apply a candidate patch in an isolated git worktree safely.

[Role] Supplies a host-neutral source integration primitive for RSI adapters.
[Invariants] Git receives argv arrays; a failed patch or build can be cleaned
             without touching the caller's checkout.
[Timeouts & Locks] Commands have a caller-provided timeout; no NPU is used.
[Side-Effect Policy] Only the requested worktree is created, patched, or
                     removed. The source checkout remains unchanged.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
import re


class WorkspaceError(RuntimeError):
    pass


class ResearchWorkspace:
    def __init__(self, source_root: str | Path, candidate_id: str,
                 workspace_root: str | Path | None = None, timeout_s: int = 120) -> None:
        self.source_root = Path(source_root).resolve()
        self.candidate_id = candidate_id
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.timeout_s = timeout_s
        self.path: Path | None = None
        self._temporary_root: Path | None = None

    def _candidate_dir(self, base: Path) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.candidate_id):
            raise WorkspaceError("candidate_id contains unsupported path characters")
        return (base / self.candidate_id).resolve()

    def _git(self, *args: str) -> str:
        try:
            result = subprocess.run(["git", *args], cwd=self.source_root,
                                    text=True, capture_output=True,
                                    timeout=self.timeout_s, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError(f"git {' '.join(args[:2])} failed: {exc}") from exc
        if result.returncode:
            raise WorkspaceError(result.stderr.strip() or f"git exited {result.returncode}")
        return result.stdout.strip()

    def create(self) -> Path:
        if self.path is not None:
            return self.path
        if self.workspace_root is None:
            self._temporary_root = Path(tempfile.mkdtemp(prefix="mindie-rsi-"))
        base = self.workspace_root or self._temporary_root
        assert base is not None
        base.mkdir(parents=True, exist_ok=True)
        self.path = self._candidate_dir(base)
        if self.path.parent != base.resolve():
            raise WorkspaceError("worktree path escaped workspace root")
        self._git("worktree", "add", "--detach", str(self.path), "HEAD")
        return self.path

    def apply_patch(self, patch: str) -> Path:
        if not isinstance(patch, str) or not patch.strip():
            raise WorkspaceError("patch must be a non-empty unified diff")
        path = self.create()
        check = subprocess.run(["git", "apply", "--check", "-"], cwd=path,
                               input=patch, text=True, capture_output=True,
                               timeout=self.timeout_s, check=False)
        if check.returncode:
            raise WorkspaceError(check.stderr.strip() or "git apply --check failed")
        result = subprocess.run(["git", "apply", "-"], cwd=path, input=patch,
                                text=True, capture_output=True,
                                timeout=self.timeout_s, check=False)
        if result.returncode:
            raise WorkspaceError(result.stderr.strip() or "git apply failed")
        return path

    def cleanup(self) -> None:
        if self.path is None:
            return
        path = self.path
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(path)],
                           cwd=self.source_root, text=True, capture_output=True,
                           timeout=self.timeout_s, check=False)
        finally:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            self.path = None
            if self._temporary_root is not None:
                shutil.rmtree(self._temporary_root, ignore_errors=True)
                self._temporary_root = None
