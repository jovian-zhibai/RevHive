#!/bin/bash
# 3-model cross-model variance cell: 10 CVE cases x {agnes, agnes-pro, sensenova-ds}
# --fallback none: the named model's answer or a failure, never silent substitution.
set -u
AGNES_KEY="sk-2B3bSUhQgryBfgi1SVKlAyKOFbCWONPlX9aXGLLCUE60htRi"
SN_KEY="$(cat ~/.config/revhive/sensenova.key)"
WORK=/tmp/bench-3model/work
OUT=/tmp/bench-3model/out
RV=/Users/souljian/.local/pipx/venvs/revhive-ai/bin/revhive
mkdir -p "$WORK" "$OUT"
note() { echo "[$(date +%H:%M:%S)] $*"; }

run_case() {
  local id="$1" repo="$2" sha="$3" label="$4" model="$5" key="$6"
  local out="$OUT/$id-$model.json"
  [ -f "$out" ] && { note "skip $id/$model (exists)"; return; }
  local dir="$WORK/$id"
  if [ ! -d "$dir/.git" ]; then
    rm -rf "$dir"; mkdir -p "$dir"; cd "$dir" || return
    git init -q .; git remote add origin "https://github.com/$repo"
    git config user.email bench@revhive.local; git config user.name bench
    if ! git fetch -q --depth 2 origin "$sha" 2>/dev/null; then note "FETCH FAIL $id"; return; fi
    git checkout -q -f FETCH_HEAD
    printf 'max_concurrent_agents: 1\n' > .revhive.yml
    git revert --no-commit HEAD >/dev/null 2>&1
    git add -A >/dev/null 2>&1; git commit -qm "reintroduce $label"
  fi
  cd "$dir" || return
  note "=== $id/$model ($label)"
  LLM_API_KEY="$key" timeout 1500 "$RV" review -d HEAD~1 -m "$model" --agents security,logic \
    --fallback none --format json -o "$out" > "$OUT/$id-$model.log" 2>&1
  note "done $id/$model rc=$?"
}

MODELS_AGNES="agnes"
MODELS_AGNES_PRO="agnes-pro"
MODELS_SN="sensenova-ds"

for m in $MODELS_AGNES; do
  for spec in "c01 django/django 3a2ec4378b53c7b8ff5283ad7eba624ffa6f2b2f SRS-local-file" \
              "c02 django/django f5b039a619b3b5394ea5b966e17c7546fda6f937 password-hash" \
              "c03 django/django c0b7e5e4e4546e8ddc58262f1945938bb9d9ac2d empty-vs-missing-headers" \
              "c04 django/django af4319f7217e33ad5db747226765f09b49ec07ba unescaped-logs" \
              "c05 django/django 5d95d9236fa4ededfb1931c60a65432385ece85f unbounded-multipart" \
              "c06 django/django 83cbd21be57483e6b3eb6e4688a561c841aff75e forged-formset" \
              "c07 django/django c88b304cc2d90fc37d3bd1f5f3829706fa6c13bc parser-memory" \
              "c08 django/django 47511a21026cdd721d8fbf8571cc079bc38bb46d unvalidated-url" \
              "c09 axios/axios c7329fefc890050edd51e40e469a154d0117fc55 axios-ssrf" \
              "c10 lodash/lodash 3469357cff396a26c363f8c1b5a91dde28ba4b1c lodash-template"; do
    set -- $spec; run_case "$1" "$2" "$3" "$4" "$m" "$AGNES_KEY"
  done
done

for m in $MODELS_AGNES_PRO; do
  for spec in "c01 django/django 3a2ec4378b53c7b8ff5283ad7eba624ffa6f2b2f SRS-local-file" \
              "c02 django/django f5b039a619b3b5394ea5b966e17c7546fda6f937 password-hash" \
              "c03 django/django c0b7e5e4e4546e8ddc58262f1945938bb9d9ac2d empty-vs-missing-headers" \
              "c04 django/django af4319f7217e33ad5db747226765f09b49ec07ba unescaped-logs" \
              "c05 django/django 5d95d9236fa4ededfb1931c60a65432385ece85f unbounded-multipart" \
              "c06 django/django 83cbd21be57483e6b3eb6e4688a561c841aff75e forged-formset" \
              "c07 django/django c88b304cc2d90fc37d3bd1f5f3829706fa6c13bc parser-memory" \
              "c08 django/django 47511a21026cdd721d8fbf8571cc079bc38bb46d unvalidated-url" \
              "c09 axios/axios c7329fefc890050edd51e40e469a154d0117fc55 axios-ssrf" \
              "c10 lodash/lodash 3469357cff396a26c363f8c1b5a91dde28ba4b1c lodash-template"; do
    set -- $spec; run_case "$1" "$2" "$3" "$4" "$m" "$AGNES_KEY"
  done
done

for m in $MODELS_SN; do
  for spec in "c01 django/django 3a2ec4378b53c7b8ff5283ad7eba624ffa6f2b2f SRS-local-file" \
              "c02 django/django f5b039a619b3b5394ea5b966e17c7546fda6f937 password-hash" \
              "c03 django/django c0b7e5e4e4546e8ddc58262f1945938bb9d9ac2d empty-vs-missing-headers" \
              "c04 django/django af4319f7217e33ad5db747226765f09b49ec07ba unescaped-logs" \
              "c05 django/django 5d95d9236fa4ededfb1931c60a65432385ece85f unbounded-multipart" \
              "c06 django/django 83cbd21be57483e6b3eb6e4688a561c841aff75e forged-formset" \
              "c07 django/django c88b304cc2d90fc37d3bd1f5f3829706fa6c13bc parser-memory" \
              "c08 django/django 47511a21026cdd721d8fbf8571cc079bc38bb46d unvalidated-url" \
              "c09 axios/axios c7329fefc890050edd51e40e469a154d0117fc55 axios-ssrf" \
              "c10 lodash/lodash 3469357cff396a26c363f8c1b5a91dde28ba4b1c lodash-template"; do
    set -- $spec; run_case "$1" "$2" "$3" "$4" "$m" "$SN_KEY"
  done
done

note "ALL DONE — summarizing"
python3 - << 'PY'
import json, glob, os
rows = []
for fp in sorted(glob.glob("/tmp/bench-3model/out/*.json")):
    try:
        d = json.load(open(fp))
    except Exception:
        continue
    fs = d.get("findings") or []
    rows.append({
        "run": os.path.basename(fp)[:-5],
        "n": len(fs),
        "crit": sum(1 for f in fs if (f.get("severity") or "").lower() == "critical"),
        "high": sum(1 for f in fs if (f.get("severity") or "").lower() == "high"),
        "withheld": d.get("risk_score_withheld"),
        "risk": d.get("risk_score"),
    })
for r in rows:
    print(r)
json.dump(rows, open("/tmp/bench-3model/out/summary.json", "w"), indent=1)
PY
