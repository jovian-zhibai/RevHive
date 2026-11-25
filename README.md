# RevHive

[![PyPI](https://img.shields.io/pypi/v/revhive-ai)](https://pypi.org/project/revhive-ai/)
[![Downloads](https://img.shields.io/pypi/dm/revhive-ai)](https://pypi.org/project/revhive-ai/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-BSL--1.1-blue)](LICENSE)
[![CI](https://github.com/jovian-zhibai/RevHive/actions/workflows/ci.yml/badge.svg)](https://github.com/jovian-zhibai/RevHive/actions)

> **Nine AI reviewers. One merge decision.**
>
> RevHive scans every pull request with 10 specialized agents (9 reviewers + 1 coordinator) for security holes, logic bugs, performance traps and test gaps — then hands you **one risk score and a deduplicated list worth reading**. No 90-comment noise. No "LGTM" gambling.

```bash
pip install revhive-ai && revhive demo   # sample report from canned data — no key, no model call
```

**Two ways to use it:**
- **CLI / CI** — run it yourself, data never leaves your machine. Free forever under BSL.
- **[GitHub App](https://github.com/apps/revhive-review)** — every PR gets auto-reviewed. Free for 50 reviews/month, no credit card.

> **Status:** early-stage and solo-built. We run RevHive on every PR we open in our own repos. False positives are the #1 thing we care about — if a finding is noise, [tell us](https://github.com/jovian-zhibai/RevHive/issues) and we'll fix the agent, not the symptom. [How we measure that, including where our own benchmark is weak](https://revhive.souljian.cn/blog/evaluating-ai-code-review.html), [the noise measurement on 20 ordinary PRs](https://revhive.souljian.cn/blog/ordinary-prs-noise.html), and [the matched control plus a correction: all ten original runs used a rate-limit fallback model](https://revhive.souljian.cn/blog/matched-control.html).

---

## Why people use it

| The problem | What RevHive does about it |
|---|---|
| Reviewing a big PR properly takes 1–2 hours of a senior dev's day | 9 specialist agents read it in parallel, report in 1–3 minutes |
| Bugs ship because "LGTM" and nobody actually looked | Every PR gets a scored, deduplicated audit — no skipped files |
| Security findings surface in prod, not in review | A dedicated Security agent hunts SQLi, XSS, secrets, weak crypto, auth flaws before merge |
| Reviewer comments are noisy and ignored | Coordinator dedups findings across agents; you read one score, drill into what matters |
| Team pays per-seat for review tools and still worries about data leaving the repo | BYOK: your code and keys go to the LLM *you* chose (DeepSeek, MiMo, Qwen, GLM, Kimi, OpenAI…), not to us |
| Chinese models? Most tools assume OpenAI | Five Chinese providers are first-class presets (DeepSeek, MiMo, Qwen, GLM, Kimi) — pick one with `--model` and the endpoint comes with it |

## Who it's for — and who it isn't

**For:** solo devs and small teams that already do PR review but want a reliable second pair of eyes; teams that want a *pre-merge quality gate* without buying per-seat SaaS; anyone who cares where their code and API keys go.

**Not (yet) for:** replacing your senior engineers' judgment; org-scale governance dashboards (on the [roadmap](#roadmap)); non-GitHub Git hosts ([roadmap](#roadmap)).

## What a report looks like

```bash
pip install revhive-ai
revhive demo
```

Read that output as a layout preview, not as evidence about review quality: `revhive demo`
prints a canned report and never calls a model. It costs nothing, needs no key, and proves
only that the pipeline installs and renders.

To run the real thing on a real diff, bring a key:

```bash
pip install revhive-ai
export LLM_API_KEY=sk-...                    # your provider's key
revhive review --model deepseek --diff HEAD~1   # or --file path/to/file.py
```

`--model` takes a preset name (`openai`, `deepseek`, `qwen`, `glm`, `kimi`, `ollama`, …) and
the matching endpoint comes with it. A real run takes 1–3 minutes and ends in a
severity-ordered, deduplicated report. [Here is a full sample report][sample] from one real run,
including the three passes that produced its score.

[sample]: https://revhive.souljian.cn/sample-report-en.html

The headline line looks like this:

```
🚨 Risk Score: CRITICAL (91/100)

1 Critical · 1 High · 8 Medium · 11 Low
```

| Score | Level | Meaning |
|-------|-------|---------|
| 0–20 | ✅ LOW | Safe to merge |
| 21–50 | ⚠️ MEDIUM | Review recommended before merge |
| 51–80 | 🔴 HIGH | Fix before merge |
| 81–100 | 🚨 CRITICAL | Do not merge |

## Get started in 3 ways

**A. CLI — real review of a file or diff (1–3 minutes)**

```bash
pip install revhive-ai
export LLM_API_KEY=your-key            # DeepSeek, MiMo, Qwen, GLM, Kimi, OpenAI…
revhive review --file src/main.py      # or: revhive review --diff HEAD~1

# Single-pass severity is noisy (the same model scored the same commits
# 45 vs 54 risk-weighted across two identical passes in our benchmarks).
# For a stable verdict, run 2-3 passes — the report comes from the
# median-risk pass and discloses every pass's score:
revhive review --diff HEAD~1 --passes 3

# Fail closed: if any agent or pass fails, findings are still listed but the
# risk score is WITHHELD (not zero) — a partial review never emits a number
# that reads as a verdict. CI gating on the score fails safe.
```

**B. GitHub App — automatic review on every PR**

1. [Install the GitHub App](https://github.com/apps/revhive-review) on the repos you care about
2. Paste your LLM key in the auto-created dashboard (DeepSeek is pre-selected — ~$0.05/review)
3. Done. Every new PR gets a review comment with the risk score.

**C. CI — as a GitHub Action or Docker step**

```bash
docker build -t revhive .
docker run --rm -e LLM_API_KEY=$LLM_API_KEY -v $(pwd):/code revhive review --file /code/src/main.py
```

<details>
<summary>Full GitHub Actions workflow (copy-paste)</summary>

```yaml
# .github/workflows/code-review.yml
name: AI Code Review
on:
  pull_request:
    types: [opened, synchronize]
jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install revhive-ai
      - name: Run RevHive Review
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}   # DeepSeek ≈ $0.05/review
          LLM_BASE_URL: https://api.deepseek.com/v1
          LLM_MODEL: deepseek-chat
        run: |
          revhive review --diff HEAD~1 --format markdown --output review_report.md
      - name: Post Review Comment
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const report = fs.readFileSync('review_report.md', 'utf8');
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: report
            });
```
</details>

---

## Pricing — honest numbers

| Tier | Price | Reviews | Agents | Inline comments | Merge gate | History | Slack | Support |
|------|-------|---------|--------|:---:|:---:|:---:|:---:|:---:|
| **Free** | $0 | 50/mo | 4 core | — | — | — | — | Community |
| **Pro** | $9/mo | Unlimited | All 9 + coordinator | ✅ | ✅ | 30 days | — | Email (48h) |
| **Business** | $19/mo | Unlimited | All 9 + coordinator | ✅ | ✅ | Permanent | ✅ | Priority (4h SLA) |

- **Free** — for trying it out and light use. 50 reviews/month is roughly one active repo.
- **Pro ($9/mo)** — the "stop bad merges" tier: inline annotations + commit status gates so a risky PR *blocks* the merge.
- **Business ($19/mo)** — for teams that treat review as part of delivery: Slack alerts, permanent history, SLA.

**CLI mode is free forever** — `pip install revhive-ai`, bring your own key, run locally or in CI for non-production use. **Under BSL 1.1, evaluation, testing, personal and open-source use are free; production use (reviewing code that ships in a commercial environment) requires a [commercial license](mailto:souljian67@gmail.com).** The one thing nobody can do: resell RevHive as a competing hosted review service. If you're a team that wants self-hosted production use, tell us what you need — that's the enterprise roadmap, and the license is the reason we can build it.

**Why BYOK?** Because you already pay your LLM provider. We charge for the orchestration that makes 9 agents behave like one disciplined reviewer — no token markup, no model lock-in. Point it at whatever model fits your budget and compliance rules:

| Provider | Typical cost / PR review |
|---|---|
| DeepSeek | ~$0.05 |
| MiMo (Xiaomi) | ~$0.05–0.15 (free credits available) |
| Qwen (Alibaba) | ~$0.05–0.10 |
| OpenAI GPT-4o | ~$0.10–0.30 |
| Anthropic Claude | ~$0.15–0.40 |

Compare that with a per-seat review subscription ($10–45/user/month) **plus** your own spend — and remember the free tier already covers a full month of light use.

## RevHive vs the alternatives

| Feature | RevHive | CodeRabbit | Sourcery | SonarQube | Copilot Review |
|---------|:---:|:---:|:---:|:---:|:---:|
| AI-driven review | ✅ | ✅ | ✅ | ❌ | ✅ |
| Multi-agent parallel review | ✅ 10 | ❌ | ❌ | ❌ | ❌ |
| Chinese LLM support (DeepSeek/MiMo/Qwen/GLM/Kimi) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Risk score (0–100) | ✅ | ✅ | ❌ | ✅ | ❌ |
| CLI local-first (code stays on your machine) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Demo mode (no API key) | ✅ | ❌ | ❌ | N/A | ❌ |
| PR inline comments | ✅ | ✅ | ✅ | ✅ | ✅ |
| Quality gate (commit status) | ✅ | ❌ | ❌ | ✅ | ❌ |
| Source-available (BSL 1.1) | ✅ | Partial | ❌ | ✅ | ❌ |
| Self-hostable | ✅ | ❌ | ❌ | ✅ | ❌ |
| IDE integration | 🔜 | ❌ | ✅ | ✅ | ✅ |

## Trust & security

We eat our own dog food — RevHive's own CI runs pip-audit and bandit on every push, and the container runs as a non-root user:

- **Dependency scanning** — `pip-audit` on every push/PR for known CVEs
- **Static analysis** — `bandit` for hardcoded secrets, unsafe deserialization, injection
- **Docker hardening** — non-root `appuser`, sensitive files excluded via `.dockerignore`

```bash
pip install pip-audit bandit && pip-audit && bandit -r src/ -ll --skip B101
```

## Roadmap

1. **Precision benchmark** — a public dataset of real PRs with our false-positive/false-negative rates. We believe review tools should publish their noise ratio.
2. **GitLab / Gitee support**
3. **Enterprise tier** — org-wide dashboards, audit log, SSO, self-hosted license with support
4. **Fix-PR workflow** — one-click PR that applies suggested fixes
5. **IDE integration**

Want something else? [Open an issue](https://github.com/jovian-zhibai/RevHive/issues) — it directly sets the order above.

---

## Reference

### The agents — 9 reviewers plus a coordinator

| Agent | Job |
|---|---|
| **SecurityAgent** | SQLi, XSS, secrets, weak crypto, auth flaws |
| **PerformanceAgent** | N+1 queries, memory leaks, algorithmic complexity |
| **LogicAgent** | Edge cases, error handling, race conditions, type safety |
| **StyleAgent** | Naming, formatting, documentation |
| **RepoAgent** | Design patterns, SOLID, module structure, testability |
| **RefactorAgent** | Code transformation, incremental migration |
| **FixAgent** | Complete corrected code with root-cause analysis |
| **TestAgent** | Unit tests, edge-case tests, security regression tests |
| **DocAgent** | API / architecture / usage docs |
| **Coordinator** | Dedupes findings, resolves agent conflicts, computes risk score, writes the report |

<details>
<summary>Supported LLM backends & configuration</summary>

| Provider | Model | Cost / review | Setup |
|---|---|---|---|
| **DeepSeek** ⭐ recommended | `deepseek-chat` | ~$0.05 | `LLM_BASE_URL=https://api.deepseek.com/v1` |
| **MiMo (Xiaomi)** | `mimo-v2.5-pro` | ~$0.05–0.15 (free credits) | `LLM_BASE_URL=https://api.xiaomimimo.com/v1` |
| OpenAI | `gpt-4o` | ~$0.10–0.30 | `LLM_BASE_URL=https://api.openai.com/v1` |
| Qwen (Alibaba) | `qwen-plus` | ~$0.05–0.10 | `LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1` |
| GLM (Zhipu) | `glm-4` | ~$0.05–0.15 | `LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4` |
| Kimi (Moonshot) | `kimi` | ~$0.05–0.15 | `LLM_BASE_URL=https://api.moonshot.cn/v1` |
| Ollama (local) | `qwen2.5-coder:7b` | free, fully offline | `LLM_BASE_URL=http://127.0.0.1:11434/v1` |
| Anthropic | `claude-sonnet-4-20250514` | ~$0.15–0.40 | `pip install -e ".[anthropic]"`, set `ANTHROPIC_API_KEY` |
| SenseNova (商汤日日新) *evaluation only* | `deepseek-v4-flash` | free token-plan quota | `LLM_BASE_URL=https://token.sensenova.cn/v1` |
| Agnes AI *evaluation only* | `agnes-3.0-flash` | free Flash promo tier | `LLM_BASE_URL=https://apihub.agnes-ai.com/v1` |

**Presets:** set `LLM_MODEL` to `deepseek` / `openai` / `qwen` / `ollama` … and RevHive auto-configures the base URL. CLI default: MiMo. GitHub App dashboard default: DeepSeek (cheapest).

**About the two free options:** SenseNova's public-beta token plan throttles hard on tokens per minute (~8K TPM per account), so large diffs will hit rate limits mid-review; Agnes AI's Flash tier is a free promotion capped at 10 requests/minute. Both are fine for evaluating RevHive on small diffs — for anything production-shaped, a paid DeepSeek key is the honest recommendation, and free tiers can change or disappear at any time.

**Env vars**

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_API_KEY` | Yes | — | API key for your LLM provider |
| `LLM_BASE_URL` | No | `https://api.xiaomimimo.com/v1` | LLM API endpoint |
| `LLM_MODEL` | No | `mimo-v2.5-pro` | Model name |

**Config file** — create `.revhive.yml` in your project root to toggle agents, set severity thresholds and ignore paths. Full example in [`README_zh.md`](README_zh.md) or `examples/`.
</details>

<details>
<summary>Supported languages</summary>

Python, JavaScript/TypeScript, Go, Rust, Java — **full** security & performance patterns. C/C++, Ruby, PHP, Swift, Kotlin — core patterns. Other languages still work via LLM understanding, with fewer specialized checks.
</details>

<details>
<summary>Architecture & project structure</summary>

```
┌─────────────┐
│ Coordinator │ ← synthesizes, dedupes, resolves conflicts, scores
└──────┬──────┘
       │ collects results from 9 parallel agents
       ▼
 Style  Security  Perf  Logic  Repo  Refactor  Fix  Test  Doc
```

```
src/revhive/
  agents/          # 10 agents (9 reviewers + coordinator)
  graph/           # LangGraph workflow orchestration
  utils/           # LLM client, dedup, config
  team/            # batch processing engine
  analysis/        # trend analysis
  demo.py          # demo mode (no API key)
  main.py          # CLI entry point
tests/              # 54+ tests: agents, workflow, demo, dedup, integration
examples/           # ready-to-run examples
```
</details>

## Contributing

All contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Found a false positive or a missed bug? That's the most valuable issue you can file.

## License

BSL 1.1 — see [LICENSE](LICENSE). Converts to Apache 2.0 on 2030-05-12.

**What the license means in practice:**
- **Free** — evaluation, testing, personal projects, open-source development, internal non-production use, and the hosted GitHub App's free tier.
- **Paid (commercial license)** — production self-hosting: using RevHive to review/gate code that ships in a commercial or business environment. [Contact us](mailto:souljian67@gmail.com) for a license.
- **Never allowed** — reselling RevHive as a competing hosted code review service.

Questions on licensing? Open an issue or email souljian67@gmail.com.
