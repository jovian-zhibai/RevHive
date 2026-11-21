#!/bin/bash
# dual-review.sh — multi-provider review with secondary recheck
#
# Pattern: run a fast primary model, then recheck silent results (0 findings)
# with a second, independent provider. Catches single-model blind spots and
# avoids tail-latency lock-in on any one provider. Works with any two
# OpenAI-compatible providers configured via MODEL_PRESETS; bring your own
# keys for both.
#
# Default pairing (evaluated 2026-11-21 on a 10-known-vulnerability harness):
#   primary   agnes-3.0-flash  — no TPM cap, stable under serial load
#   recheck   sensenova-ds (DeepSeek V4.1) — highest recall, incl. cases the
#             primary missed; subject to small-prompt rate limits
#
# Env vars:
#   AGNES_API_KEY      key for the primary provider
#   SENSENOVA_API_KEY  key for the recheck provider
#
# 用法: ./dual-review.sh <git-diff 参数,如 -d HEAD~1> [其他 revhive 参数...]
#   例: ./dual-review.sh -d HEAD~1 --agents security,logic
#
# 输出: <output>/final.json （两引擎合并后的报告）
set -euo pipefail

AGNES_KEY="${AGNES_API_KEY:-}"
SN_KEY="${SENSENOVA_API_KEY:-}"
OUTDIR="${DUAL_REVIEW_OUT:-./.revhive-dual}"
mkdir -p "$OUTDIR"

if [ -z "$AGNES_KEY" ]; then echo "错误: 未设置 AGNES_API_KEY" >&2; exit 1; fi

echo "[1/2] 第一引擎: Agnes agnes-3.0-flash ..."
LLM_API_KEY="$AGNES_KEY" revhive review "$@" -m agnes --format json -o "$OUTDIR/agnes.json"

PRIMARY_FINDINGS=$(python3 - "$OUTDIR/agnes.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
fs=[f for f in (d.get("findings") or []) if "No security vulnerabilities" not in (f.get("title") or "")]
print(len(fs))
PY
)
echo "    Agnes 报告 $PRIMARY_FINDINGS 条 findings"

if [ "$PRIMARY_FINDINGS" -gt 0 ]; then
  echo "[2/2] Agnes 已给出结论，跳过第二引擎。"
  cp "$OUTDIR/agnes.json" "$OUTDIR/final.json"
  echo "完成: $OUTDIR/final.json"
  exit 0
fi

if [ -z "$SN_KEY" ]; then
  echo "[2/2] 未设置 SENSENOVA_API_KEY，无第二引擎可复查。"
  cp "$OUTDIR/agnes.json" "$OUTDIR/final.json"
  echo "完成: $OUTDIR/final.json"
  exit 0
fi

echo "[2/2] Agnes 静默，第二引擎: SenseNova deepseek-flash (DeepSeek V4.1) 复查 ..."
# 注意：需在待审查仓库目录内执行本脚本，第二引擎继承同一 diff 参数。
LLM_API_KEY="$SN_KEY" revhive review "$@" -m sensenova-ds --format json -o "$OUTDIR/sensenova.json" || \
  echo "    警告: 第二引擎失败（免费档限流），最终报告以 Agnes 为准" >&2

python3 - "$OUTDIR" <<'PY'
import json,sys,os
out=sys.argv[1]
pa=os.path.join(out,"agnes.json"); ps=os.path.join(out,"sensenova.json")
a=json.load(open(pa)) if os.path.exists(pa) else {"findings":[]}
keep=[f for f in (a.get("findings") or []) if "No security vulnerabilities" not in (f.get("title") or "")]
if os.path.exists(ps):
    try:
        b=json.load(open(ps))
        extra=[f for f in (b.get("findings") or [])
               if "No security vulnerabilities" not in (f.get("title") or "")]
        seen={(f.get("title") or "")[:60] for f in keep}
        for f in extra:
            if (f.get("title") or "")[:60] not in seen:
                f=dict(f); f["agent"]=f.get("agent","")+" [sensenova-recheck]"
                keep.append(f)
    except Exception as e:
        print(f"    第二引擎结果解析失败: {e}", file=sys.stderr)
a["findings"]=keep
a["total_findings"]=len(keep)
a["summary"]=(a.get("summary") or "")+f"\n\n[dual-review] Agnes 主审 + SenseNova V4.1 复查，合并 {len(keep)} 条。"
json.dump(a, open(os.path.join(out,"final.json"),"w"), ensure_ascii=False, indent=1)
print(f"    合并完成: {len(keep)} 条 findings")
PY
echo "完成: $OUTDIR/final.json"
