#!/usr/bin/env python3
"""Analyze the 3-pass benchmark: cross-pass variance with the reconciliation pipeline live."""
import json, glob, os, re, statistics, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/passes3"
rows = []
for fp in sorted(glob.glob(OUT + "/*.json")):
    sha = os.path.basename(fp)[:-5]
    try:
        d = json.load(open(fp))
    except Exception:
        continue
    s = d.get("summary", "")
    m = re.search(r"Consensus of 3 independent passes — risk scores: pass 1: (\d+|failed), pass 2: (\d+|failed), pass 3: (\d+|failed)", s)
    if not m:
        print(f"{sha}: NO CONSENSUS BLOCK — {s[:80]!r}")
        continue
    scores = []
    for g in m.groups():
        scores.append(None if g == "failed" else int(g))
    ok = [x for x in scores if x is not None]
    caps = re.findall(r"capped (\w+)->(\w+) \(([^)]+)\): (.{0,50})", s)
    anchored = len(re.findall(r"anchored to", s))
    findings = [(f["severity"], f.get("file_path"), f["title"][:40]) for f in d.get("findings", [])]
    spread = (max(ok) - min(ok)) if ok else None
    rows.append({"sha": sha, "scores": scores, "spread": spread, "caps": caps,
                 "anchored": anchored, "findings": findings})

if not rows:
    print("no data yet")
    sys.exit(0)

spreads = [r["spread"] for r in rows if r["spread"] is not None]
print(f"runs: {len(rows)}, with all-3-pass scores: {len(spreads)}")
if spreads:
    print(f"cross-pass spread: median={statistics.median(spreads)}, mean={statistics.mean(spreads):.1f}, max={max(spreads)}")
    hist = {"0": 0, "1-10": 0, "11-20": 0, "21-40": 0, ">40": 0}
    for s in spreads:
        if s == 0: hist["0"] += 1
        elif s <= 10: hist["1-10"] += 1
        elif s <= 20: hist["11-20"] += 1
        elif s <= 40: hist["21-40"] += 1
        else: hist[">40"] += 1
    print("spread histogram:", hist)
total_caps = sum(len(r["caps"]) for r in rows)
anchored_runs = sum(1 for r in rows if r["anchored"])
print(f"cap adjustments total: {total_caps} across {len(rows)} runs; runs with anchored findings: {anchored_runs}")
print()
for r in rows:
    print(f"{r['sha']}: passes={r['scores']} spread={r['spread']} caps={len(r['caps'])}")
    for sev, path, title in r["findings"]:
        if sev in ("critical", "high"):
            print(f"    [{sev}] {path or 'NO-PATH'} | {title}")
