"""Privacy guard: this repo is public, and real holdings and credentials must never
be tracked. Fails the suite the moment one is staged or committed."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return out.stdout.splitlines()


def test_no_holdings_snapshot_is_tracked():
    leaked = [p for p in _tracked() if Path(p).name.startswith("holdings")
              and p.startswith("data/")]
    assert leaked == [], f"real holdings must stay untracked: {leaked}"


def test_no_service_account_key_is_tracked():
    leaked = [p for p in _tracked() if p.endswith(".json")
              and '"private_key"' in (ROOT / p).read_text(encoding="utf-8", errors="ignore")]
    assert leaked == [], f"credentials must stay untracked: {leaked}"


def test_the_holdings_snapshot_path_is_gitignored():
    from stockanalysis.holdings import DEFAULT_SNAPSHOT
    res = subprocess.run(["git", "check-ignore", "-q", str(DEFAULT_SNAPSHOT)], cwd=ROOT)
    assert res.returncode == 0, f"{DEFAULT_SNAPSHOT} must be gitignored"
