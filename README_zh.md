# RevHive

[![PyPI](https://img.shields.io/pypi/v/revhive-ai)](https://pypi.org/project/revhive-ai/)
[![Downloads](https://img.shields.io/pypi/dm/revhive-ai)](https://pypi.org/project/revhive-ai/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-BSL--1.1-blue)](LICENSE)
[![CI](https://github.com/jovian-zhibai/RevHive/actions/workflows/ci.yml/badge.svg)](https://github.com/jovian-zhibai/RevHive/actions)

> **10 个 AI 审查员 · 30 秒 · 一个合并决策**
>
> RevHive 用 10 个专业 Agent（9 个审查 + 1 个汇总）扫描每个 PR：安全漏洞、逻辑 Bug、性能隐患、测试缺口，最终给你**一个风险评分 + 一份去重后值得读的发现清单**。没有 90 条噪音评论，没有"LGTM"式碰运气。

```bash
pip install revhive-ai && revhive demo   # 30 秒体验——无需 API Key、无需配置
```

**两种用法：**
- **CLI / CI** — 自己跑，数据不出你的机器。BSL 许可下永久免费。
- **[GitHub App](https://github.com/apps/revhive-bot)** — 每个 PR 自动审查。每月免费 50 次，无需绑卡。

> **状态：** 早期项目，独立开发中。我们在自己所有仓库的每个 PR 上都跑 RevHive。**误报是我们最在意的事**——如果某条发现是噪音，[告诉我们](https://github.com/jovian-zhibai/RevHive/issues)，我们会修 Agent 本身，而不是掩盖症状。

---

## 为什么用 RevHive

| 痛点 | RevHive 的解法 |
|---|---|
| 认真审一个大 PR 要耗掉资深工程师 1–2 小时 | 10 个 Agent 并行读代码，约 30 秒出报告 |
| "LGTM" 文化让 Bug 溜进主干 | 每个 PR 都有打分、去重后的完整审计，没有文件被跳过 |
| 安全问题总是上线后才暴露 | 专职 Security Agent 在合并前排查 SQLi、XSS、密钥泄露、弱加密、认证缺陷 |
| 审查评论太吵，最后没人看 | Coordinator 跨 Agent 语义去重；你只看一个分数，按需下钻 |
| 按席位买审查工具，还担心代码出境 | BYOK：代码和 Key 只去**你自己选的**大模型（DeepSeek/MiMo/Qwen/GLM/Kimi/OpenAI…），不经我们 |
| 中文模型支持差？多数工具只认 OpenAI | 5 家国产 LLM 一等公民——这是目前唯一原生支持 DeepSeek/MiMo/Qwen/GLM/Kimi 的审查工具 |

## 适合谁 —— 以及暂时不适合谁

**适合：** 已经在做 PR 审查、但想要一双可靠"第二眼睛"的独立开发者和小团队；想把「合并前质量门禁」落地、又不想按席位买 SaaS 的团队；在意代码与 API Key 去向的任何人。

**暂时不适合：** 替代资深工程师的判断；组织级治理看板（见[路线图](#路线图)）；非 GitHub 的 Git 托管平台（见[路线图](#路线图)）。

## 30 秒看效果

```bash
pip install revhive-ai
revhive demo
```

Demo 用本地模拟响应跑完整 10-Agent 流程，报告结构与真实运行完全一致，零成本。最终会给你一份按严重度排序、已去重的报告：

```
🚨 Risk Score: CRITICAL (91/100)

1 Critical · 1 High · 8 Medium · 11 Low
```

| 分数 | 等级 | 含义 |
|------|------|------|
| 0–20 | ✅ LOW | 可以放心合并 |
| 21–50 | ⚠️ MEDIUM | 建议审查后再合并 |
| 51–80 | 🔴 HIGH | 修复后再合并 |
| 81–100 | 🚨 CRITICAL | 不建议合并 |

## 三种方式上手

**A. CLI — 真实审查文件或 diff（30 秒）**

```bash
pip install revhive-ai
export LLM_API_KEY=你的Key            # DeepSeek / MiMo / Qwen / GLM / Kimi / OpenAI…
revhive review --file src/main.py      # 或：revhive review --diff HEAD~1
```

**B. GitHub App — 每个 PR 自动审查**

1. 在目标仓库上[安装 GitHub App](https://github.com/apps/revhive-bot)
2. 在自动创建的 dashboard 里粘贴 LLM Key（默认已选 DeepSeek——约 $0.05/次审查）
3. 完成。每个新 PR 都会自动收到带风险评分的审查评论。

**C. CI — GitHub Action 或 Docker**

```bash
docker build -t revhive .
docker run --rm -e LLM_API_KEY=$LLM_API_KEY -v $(pwd):/code revhive review --file /code/src/main.py
```

<details>
<summary>完整 GitHub Actions workflow（可直接复制）</summary>

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
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}   # DeepSeek ≈ $0.05/次
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

## 价格 — 把账算明白

| 档位 | 价格 | 审查次数 | Agent | Inline 评论 | 提交状态门禁 | 历史 | Slack | 支持 |
|------|------|---------|--------|:---:|:---:|:---:|:---:|:---:|
| **Free** | $0 | 50/月 | 4 个核心 | — | — | — | — | 社区 |
| **Pro** | $12/月 | 不限 | 全部 10 个 | ✅ | ✅ | 30 天 | — | 邮件 (48h) |
| **Business** | $25/月 | 不限 | 全部 10 个 | ✅ | ✅ | 永久 | ✅ | 优先 (4h SLA) |

- **Free** — 试用与轻度使用。50 次/月 ≈ 一个活跃仓库。
- **Pro ($12/月)** — 「拦住烂合并」档：inline 标注 + 提交状态门禁，高风险 PR 直接**阻止合并**。
- **Business ($25/月)** — 把审查当交付环节的团队：Slack 通知、永久历史、SLA。

**CLI 模式免费** — `pip install revhive-ai`，自带 Key，本地或 CI 跑非生产用途。按 BSL 1.1：**评估、测试、个人项目、开源开发免费；生产用途（审查进入商业环境的代码）需[商业授权](mailto:souljian67@gmail.com)**。任何人都不允许的只有一件事：把 RevHive 作为竞争性托管审查服务转售。如果你是需要生产自托管的团队，告诉我们你需要什么——那就是企业路线图，而这份许可正是我们能把它做下去的原因。

**为什么 BYOK？** 因为你本来就在为 LLM 付费。我们只收"让 10 个 Agent 表现得像一个自律审查员"的编排费——不加价 token、不锁定模型。预算和合规说了算，想用哪个模型都行：

| 服务商 | 每次 PR 审查成本 |
|---|---|
| DeepSeek | ~$0.05 |
| MiMo（小米） | ~$0.05–0.15（有免费额度） |
| Qwen（通义） | ~$0.05–0.10 |
| OpenAI GPT-4o | ~$0.10–0.30 |
| Anthropic Claude | ~$0.15–0.40 |

对比按席位的审查订阅（$10–45/用户/月）**还要再自己付 token**——而且免费层已经够轻度用户用一整月。

## 与同类工具对比

| 能力 | RevHive | CodeRabbit | Sourcery | SonarQube | Copilot Review |
|---------|:---:|:---:|:---:|:---:|:---:|
| AI 驱动审查 | ✅ | ✅ | ✅ | ❌ | ✅ |
| 多 Agent 并行审查 | ✅ 10 | ❌ | ❌ | ❌ | ❌ |
| 国产 LLM（DeepSeek/MiMo/Qwen/GLM/Kimi） | ✅ | ❌ | ❌ | ❌ | ❌ |
| 风险评分 (0–100) | ✅ | ✅ | ❌ | ✅ | ❌ |
| CLI 本地优先（代码不出机器） | ✅ | ❌ | ❌ | ❌ | ❌ |
| Demo 模式（无需 API Key） | ✅ | ❌ | ❌ | N/A | ❌ |
| PR Inline 评论 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 质量门禁（commit status） | ✅ | ❌ | ❌ | ✅ | ❌ |
| 源码可得（BSL 1.1） | ✅ | Partial | ❌ | ✅ | ❌ |
| 可自托管 | ✅ | ❌ | ❌ | ✅ | ❌ |
| IDE 集成 | 🔜 | ❌ | ✅ | ✅ | ✅ |

## 信任与安全

我们自己吃自己的狗粮——RevHive 的 CI 每次推送都跑 pip-audit 和 bandit，容器以非 root 用户运行：

- **依赖扫描** — 每次推送/PR 用 `pip-audit` 查已知 CVE
- **静态分析** — `bandit` 查硬编码密钥、不安全反序列化、注入风险
- **Docker 加固** — 非 root `appuser`，敏感文件经 `.dockerignore` 排除

```bash
pip install pip-audit bandit && pip-audit && bandit -r src/ -ll --skip B101
```

## 路线图

1. **精度基准** — 公开一份真实 PR 数据集 + 我们的误报/漏报率。审查工具就该公布自己的噪音比。
2. **GitLab / Gitee 支持**
3. **企业版** — 组织级看板、审计日志、SSO、带支持的自托管授权
4. **Fix-PR 工作流** — 一键提交应用建议修复的 PR
5. **IDE 集成**

想要别的？[开一个 issue](https://github.com/jovian-zhibai/RevHive/issues)——它会直接决定上面的优先级。

---

## 参考

### 10 个 Agent

| Agent | 职责 |
|---|---|
| **SecurityAgent** | SQL 注入、XSS、密钥泄露、弱加密、认证缺陷 |
| **PerformanceAgent** | N+1 查询、内存泄漏、算法复杂度 |
| **LogicAgent** | 边界条件、异常处理、竞态条件、类型安全 |
| **StyleAgent** | 命名规范、格式、注释文档 |
| **RepoAgent** | 设计模式、SOLID、模块结构、可测试性 |
| **RefactorAgent** | 代码转换、渐进式重构 |
| **FixAgent** | 完整修复代码 + 根因分析 |
| **TestAgent** | 单元测试、边界用例、安全回归测试 |
| **DocAgent** | API/架构/使用文档 |
| **Coordinator** | 语义去重、Agent 冲突解决、风险评分、报告生成 |

### 支持的 LLM 后端

| 服务商 | 模型 | 每次成本 | 配置 |
|---|---|---|---|
| **DeepSeek** | `deepseek-chat` | ~$0.05 | `LLM_BASE_URL=https://api.deepseek.com/v1` |
| **MiMo (小米)** | `mimo-v2.5-pro` | ~$0.05–0.15（有免费额度） | `LLM_BASE_URL=https://api.xiaomimimo.com/v1` |
| OpenAI | `gpt-4o` | ~$0.10–0.30 | `LLM_BASE_URL=https://api.openai.com/v1` |
| Qwen (通义) | `qwen-plus` | ~$0.05–0.10 | `LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1` |
| GLM (智谱) | `glm-4` | ~$0.05–0.15 | `LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4` |
| Kimi (月之暗面) | `kimi` | ~$0.05–0.15 | `LLM_BASE_URL=https://api.moonshot.cn/v1` |
| Anthropic | `claude-sonnet-4-20250514` | ~$0.15–0.40 | `pip install -e ".[anthropic]"`，设置 `ANTHROPIC_API_KEY` |

**快速预设：** `LLM_MODEL` 设为 `deepseek` / `openai` / `qwen` 等预设名即自动配置 base URL。CLI 默认 MiMo；GitHub App dashboard 默认 DeepSeek（最便宜）。

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `LLM_API_KEY` | **是** | — | LLM 服务商 API Key |
| `LLM_BASE_URL` | 否 | `https://api.xiaomimimo.com/v1` | LLM API 端点 |
| `LLM_MODEL` | 否 | `mimo-v2.5-pro` | 模型名 |

### 配置

项目根目录创建 `.revhive.yml`：

```yaml
model: mimo-v2.5-pro

agents:
  style:      { enabled: true }
  security:   { enabled: true, severity_threshold: medium }  # 只报 medium 及以上
  performance:{ enabled: true }
  logic:      { enabled: true }
  repo:       { enabled: true }
  refactor:   { enabled: true }
  fix:        { enabled: true }
  test:       { enabled: true }
  doc:        { enabled: false }       # 关闭文档 Agent

ignore:                        # glob 模式 — ** 匹配任意层级
  - "*.min.js"
  - "*.min.css"
  - "vendor/**"
  - "node_modules/**"
  - "migrations/**"
  - "__pycache__/**"
  - ".git/**"
  - ".venv/**"
```

### 支持的语言

| 语言 | 扩展名 | 安全模式 | 性能模式 |
|------|--------|---------|---------|
| Python | .py | ✅ 完整 | ✅ 完整 |
| JavaScript/TypeScript | .js .jsx .mjs .ts .tsx | ✅ 完整 | ✅ 完整 |
| Go | .go | ✅ 完整 | ✅ 完整 |
| Rust | .rs | ✅ 完整 | ✅ 完整 |
| Java | .java | ✅ 完整 | ✅ 完整 |
| C/C++ | .c .cpp .h .hpp | ✅ 核心 | ⚠️ 基础 |
| Ruby | .rb | ✅ 核心 | ⚠️ 基础 |
| PHP | .php | ✅ 完整 | ⚠️ 基础 |
| Swift | .swift | ✅ 核心 | ⚠️ 基础 |
| Kotlin | .kt | ✅ 核心 | ⚠️ 基础 |

其他语言可通过 LLM 理解审查，但专用检测模式较少。

### 架构与项目结构

```
┌─────────────┐
│ Coordinator │ ← 汇总、去重、解决冲突、评分
└──────┬──────┘
       │ 收集 9 个并行 Agent 的结果
       ▼
 Style  Security  Perf  Logic  Repo  Refactor  Fix  Test  Doc
```

```
src/revhive/
  agents/          # 10 个 Agent（9 审查 + 1 汇总）
  graph/           # LangGraph 工作流编排
  utils/           # LLM 客户端、去重、配置
  team/            # 批量处理引擎
  analysis/        # 历史趋势分析
  demo.py          # Demo 模式（无需 API Key）
  main.py          # CLI 入口
tests/              # 54+ 测试：agents、workflow、demo、去重、集成
examples/           # 开箱即用示例
```

## 贡献

欢迎一切贡献，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。发现误报或漏报？那是最有价值的 issue。

## License

BSL 1.1 — 详见 [LICENSE](LICENSE)。2030-05-12 自动转为 Apache 2.0。

**许可的实际含义：**
- **免费** — 评估、测试、个人项目、开源开发、内部非生产用途，以及托管 GitHub App 的免费层。
- **付费（商业授权）** — 生产自托管：用 RevHive 审查/把关进入商业环境的代码。[联系我们](mailto:souljian67@gmail.com)获取授权。
- **任何时候都不允许** — 把 RevHive 作为竞争性托管代码审查服务转售。

许可问题？开 issue 或发邮件 souljian67@gmail.com。
