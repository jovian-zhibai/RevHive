#!/usr/bin/env python3
"""Analyze the 3-model x 10-CVE cell.

Questions this answers:
1. Cross-model hit agreement: which cases does each model catch?
2. Finding-count / severity distribution per model on true-positive cases.
3. Sensenova-ds viability with fallback disabled (completion rate).
"""
import json, glob, os, re, sys

OUT = "/tmp/bench-3model/out"
MODELS = ["agnes", "agnes-pro", "sensenova-ds"]
CASES = [f"c{i:02d}" for i in range(1, 11)]
# ground truth: c02 was missed by agnes-3.0-flash historically; c07 partial.
EXPECTED = {  # case -> keyword expected in a true-positive finding title/desc
    "c01": ["ssrf", "local file", "srs", "spatialreference"],
    "c02": ["password", "hash"],
    "c03": ["header"],
    "c04": ["log"],
    "c05": ["multipart", "memory"],
    "c06": ["formset", "forged"],
    "c07": ["parser", "memory", "translation"],
    "c08": ["url", "ssrf"],
    "c09": ["ssrf"],
    "c10": ["template", "command", "injection"],
}

def load(model, case):
    fp = f"{OUT}/{case}-{model}.json"
    if not os.path.exists(fp):
        return None
    try:
        return json.load(open(fp))
    except Exception:
        return None

def hit(d, case):
    if not d:
        return False
    kws = EXPECTED[case]
    for f in d.get("findings") or []:
        text = ((f.get("title") or "") + " " + (f.get("description") or "") + " " + (f.get("file_path") or "")).lower()
        if any(k in text for k in kws):
            return True
    return False

print("=== hit matrix (case x model) ===")
hdr = "case  " + "  ".join(f"{m:12s}" for m in MODELS)
print(hdr)
matrix = {}
for c in CASES:
    row = []
    for m in MODELS:
        d = load(m, c)
        ok = hit(d, c)
        matrix[(c, m)] = ok
        row.append("HIT " if ok else ("FAIL" if d is not None else "no-run"))
    print(f"{c}   " + "  ".join(f"{x:12s}" for x in row))

print("\n=== per-model stats ===")
for m in MODELS:
    runs = [load(m, c) for c in CASES]
    completed = sum(1 for d in runs if d is not None)
    hits = sum(1 for c in CASES if matrix[(c, m)])
    withheld = sum(1 for d in runs if d and d.get("risk_score_withheld"))
    n_findings = [len(d.get("findings") or []) for d in runs if d]
    print(f"{m:14s} completed {completed}/10  hits {hits}/10  withheld {withheld}  "
          f"findings/run mean {sum(n_findings)/max(1,len(n_findings)):.1f}")

print("\n=== pairwise model agreement on hits ===")
for a, b in [("agnes", "agnes-pro"), ("agnes", "sensenova-ds"), ("agnes-pro", "sensenova-ds")]:
    both = sum(1 for c in CASES if matrix[(c, a)] and matrix[(c, b)])
    only_a = sum(1 for c in CASES if matrix[(c, a)] and not matrix[(c, b)])
    only_b = sum(1 for c in CASES if not matrix[(c, a)] and matrix[(c, b)])
    print(f"{a} vs {b}: agree {both}, only-{a} {only_a}, only-{b} {only_b}")
