"""Test helpers: redirect the stores to a temp directory.

The real store writes to out/ and vault/. Tests must not depend on, or
clobber, a demo run sitting in the working tree.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mhb import store  # noqa: E402


class TempStore:
    def __init__(self):
        self.dir = None
        self._saved = None

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="mhb-test-"))
        self._saved = (store.OUT, store.CASES, store.VAULT, store.AUDIT)
        store.OUT = self.dir / "out"
        store.CASES = store.OUT / "cases"
        store.VAULT = self.dir / "vault"
        store.AUDIT = store.OUT / "audit.jsonl"
        store.ensure_dirs()
        return self

    def __exit__(self, *exc):
        store.OUT, store.CASES, store.VAULT, store.AUDIT = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)
        return False
