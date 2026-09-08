## RevHive v0.4.0 — License clarity, brand consolidation, buyer-first docs

This release makes the terms of using RevHive explicit, completes the move to the
`jovian-zhibai` organization, and rewrites the documentation around what buyers and
teams actually need to decide.

### ⚠️ License change — read before upgrading (not retroactive)

The BSL 1.1 **Additional Use Grant has been narrowed**. Previous releases granted
production self-hosting for free; **v0.4.0 does not.**

| Use case | Old grant | v0.4.0 grant |
|---|---|---|
| Evaluation, testing, personal / OSS projects, teaching | Free | **Free** (unchanged) |
| Internal non-production use (dev / staging) | Free | **Free** (unchanged) |
| Self-hosted production use in a business environment | Free | **Requires a commercial license** |
| Delivering RevHive as a competing hosted review service | Never | **Never** (unchanged) |

- **Not retroactive:** releases published before v0.4.0 remain governed by the grant
  they shipped with. The new grant applies to v0.4.0 and later.
- Commercial licensing (Self-Host $690/yr for one org, Enterprise quoted on
  request incl. OEM/embedding rights): see [docs/commercial-license.html](https://revhive.souljian.cn/commercial-license.html)
  or email souljian67@gmail.com.
- The hosted GitHub App (Pro / Business subscriptions) is unaffected — a subscription
  already covers production use of the hosted service.

### 🔀 Brand consolidation

- All repository URLs, PyPI metadata, CLI output links, sponsor links and landing-page
  links now point to **jovian-zhibai/RevHive**. The `Jansen003` copies are deprecated
  and will be archived.
- Repository description and topics updated for discoverability.

### 📄 New documentation

- **Commercial license page** — free / paid / never-allowed boundaries, pricing, FAQ,
  and an ROI section explaining when self-hosting beats the hosted service.
- **Privacy policy (draft)** — hosted App data flow, 30-day diff retention, no model
  training on your code, BYOK provider disclosure.
- **Terms of service (draft)** — subscriptions, acceptable use, AI-output disclaimer.
- **README rewrite (EN/ZH)** — buyer-first: who it's for / not for, pricing by
  job-to-be-done, BYOK framed as a customer benefit.

### 🐛 Fixed

- **server:** `FREE_TIER_MONTHLY_LIMIT` env var with an invalid value silently fell
  back to **5** instead of the documented **50** — free-tier users could lose 90% of
  their monthly quota on a typo. ([cbe3ca8])
- server sponsor links hardcoded to the old account now point to jovian-zhibai.

### 🔧 Internal

- `pyproject.toml` project.urls corrected (Homepage / Documentation / Repository / Issues).
- `.env.example` synced with new sponsor login.

**Full changelog:** https://github.com/jovian-zhibai/RevHive/compare/v0.3.11...v0.4.0
