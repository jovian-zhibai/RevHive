#!/usr/bin/env python3
"""verify_run.py — decide whether a benchmark cell measured what it claims.

A review can quietly stop being the measurement it advertises:

  * the provider returns 429 and the agent switches to a fallback model
    ("rate-limited on X — switched to Y and retrying");
  * structured output fails and findings are recovered with a regex
    ("falling back to regex"), which loses severities and deflates the
    risk score;
  * the agent never produced a review at all ("failed: request timed out").

Any of those makes the cell incomparable with a clean cell, so it must be
excluded from an aggregate or reported as degraded — never averaged in
silently. This script reads the run's own logs, where the CLI already wrote
all three events, and prints a verdict per cell.

Usage:
    python scripts/verify_run.py benchmark-runs/2026-11-21-3model
    python scripts/verify_run.py benchmark-runs/2026-11-21-3model --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SWITCH = re.compile(r"rate-limited on (\S+) — switched to (\S+)")
REGEX_FALLBACK = re.compile(r"falling back to regex")
RATE_LIMIT = re.compile(r"Error code: 429|rate-limited by provider")
AGENT_FAILED = re.compile(r"\bfailed: |LLM call failed")

CLEAN = "clean"
DEGRADED = "degraded"
FAILED = "failed"


def classify(log_path: Path) -> dict:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    switches = [{"from": a, "to": b} for a, b in SWITCH.findall(text)]
    regex_hits = len(REGEX_FALLBACK.findall(text))
    rate_hits = len(RATE_LIMIT.findall(text))
    failed_hits = len(AGENT_FAILED.findall(text))

    reasons = []
    if switches:
        reasons.append(f"{len(switches)} model switch(es)")
    if regex_hits:
        reasons.append(f"{regex_hits} regex fallback(s)")
    if failed_hits:
        reasons.append(f"{failed_hits} agent failure(s)")

    if failed_hits:
        verdict = FAILED
    elif switches or regex_hits:
        verdict = DEGRADED
    else:
        verdict = CLEAN

    return {
        "verdict": verdict,
        "reasons": reasons,
        "model_switches": switches,
        "regex_fallbacks": regex_hits,
        "rate_limit_hits": rate_hits,
        "agent_failures": failed_hits,
    }


def load_output(json_path: Path) -> dict:
    if not json_path.exists():
        return {}
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        "findings": data.get("total_findings"),
        "risk": data.get("risk_score"),
        "withheld": data.get("risk_score_withheld"),
        "failed": data.get("failed"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify benchmark run provenance")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if not args.run_dir.is_dir():
        print(f"not a directory: {args.run_dir}", file=sys.stderr)
        return 2

    rows = []
    for log_path in sorted(args.run_dir.glob("*.log")):
        cell = log_path.stem
        info = classify(log_path)
        info.update(load_output(log_path.with_suffix(".json")))
        info["cell"] = cell
        rows.append(info)

    if not rows:
        print(f"no .log files under {args.run_dir}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    width = max(len(r["cell"]) for r in rows)
    print(f"{'cell'.ljust(width)}  {'verdict':8}  {'n':>3}  {'risk':>4}  why")
    print("-" * (width + 40))
    for r in rows:
        n = "-" if r.get("findings") is None else str(r.get("findings"))
        risk = "withheld" if r.get("withheld") else ("-" if r.get("risk") is None else str(r.get("risk")))
        why = "; ".join(r["reasons"])
        print(f"{r['cell'].ljust(width)}  {r['verdict']:8}  {n:>3}  {risk:>4}  {why}")

    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in (CLEAN, DEGRADED, FAILED)}
    print()
    print(f"clean {counts[CLEAN]} / degraded {counts[DEGRADED]} / failed {counts[FAILED]}"
          f"  (total {len(rows)})")
    if counts[DEGRADED] or counts[FAILED]:
        print("Aggregate only the clean cells: a switched model, a regex parse, or a")
        print("failed agent is a different measurement, not a data point.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
