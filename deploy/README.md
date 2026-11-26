# RevHive 私有化部署

目标：在你的内网跑起来，代码一行都不出网。整个过程约 10 分钟。

## 前置条件

| 项 | 要求 |
|---|---|
| Docker（可选） | 20.10+，含 `docker compose` v2 插件；走下面「零」的 pip 路线则不需要 |
| 内存 | 4 GB（接外部模型 API）/ 16 GB（本地跑 14B 模型） |
| 网络 | 出站只能访问你选择的那一家模型 API；其余全封闭 |
| 磁盘 | 2 GB（不含本地模型） |

不需要 Python，不需要 GPU（用 API 模式时）。

> 已在 `linux/amd64` 与 `linux/arm64` 两个平台各构建一次并跑过真实审查（2026-11-26）。下面的 Docker 路线是**可选项之一**，不是唯一路线。

## 零、不用 Docker 的装法（甲方不让装容器时用这个）

RevHive 的 CLI 不需要数据库、不需要常驻服务、不开端口，跑完就退出。所以最轻的私有化其实是：

```bash
python3 -m venv /opt/revhive && . /opt/revhive/bin/activate
pip install revhive-ai

export LLM_API_KEY=sk-xxxxxxxx
export LLM_BASE_URL=https://api.deepseek.com/v1
export LLM_MODEL=deepseek-chat

revhive --version
revhive review -d HEAD~1 --format markdown -o report.md
```

- **定时跑**：`cron` 或 `systemd timer` 里调上面那行 `review`，和普通脚本没区别
- **CI 里跑**：直接当命令执行，不需要 compose（见第四节）
- **完全离线安装**：先在一台能上网的机器 `pip download --only-binary=:all: -d wheels revhive-ai`（实测 49 个包、14 MB，全部是 wheel），把 `wheels/` 拷进内网，再 `pip install --no-index --find-links=wheels revhive-ai`。**下载机要和目标机同架构、同 Python 小版本**（macOS 上下载的包装不到 Linux 上）
- **隔离性**：这条路没有容器边界，靠的是"代码只发往 `LLM_BASE_URL`"这一条。要更强隔离再用下面的 Docker 路线

## 一、配置

```bash
cd deploy
cp .env.example .env
```

编辑 `.env`，二选一：

**A. 接国产模型 API**（代码只发往这一家，可写进合同）

```ini
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

| 模型 | BASE_URL | MODEL |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4` |
| Kimi | `https://api.moonshot.cn/v1` | `kimi` |

**B. 完全离线**（模型跑在本机，零外联）

```ini
LLM_API_KEY=ollama
LLM_BASE_URL=http://ollama:11434/v1
LLM_MODEL=qwen2.5-coder:14b
```

## 二、部署并自检

```bash
bash install.sh /path/to/your/repo
```

脚本会：检查 Docker → 校验配置 → 构建镜像 → 跑一次 demo 自检。
自检通过会生成 `deploy/reports/selftest.md`。

## 三、日常使用

```bash
# 审查单个文件
docker compose run --rm revhive review -f src/main.py --format markdown -o /reports/main.md

# 审查某个 commit 的改动（CI 里最常用）
docker compose run --rm revhive review -d HEAD~1 --format markdown -o /reports/diff.md

# 输出 JSON，供看板或工单系统消费
docker compose run --rm revhive review -d origin/main --format json -o /reports/diff.json
```

报告写入 `deploy/reports/`，在宿主机上可直接打开。

## 四、接进 CI（GitLab / Jenkins 通用）

```yaml
# .gitlab-ci.yml
revhive:
  stage: test
  script:
    - cd deploy
    - docker compose run --rm revhive review -d "$CI_MERGE_REQUEST_DIFF_BASE_SHA" \
        --format json -o /reports/mr.json
  artifacts:
    paths: [deploy/reports/]
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

配合 `.revhive.yml` 可以把低于阈值的发现设为阻断（见项目根目录配置说明）。

## 五、安全边界（可写进合规材料）

- 容器**不发布任何端口**，不监听网络
- 唯一的出站流量指向 `LLM_BASE_URL`
- 待审查仓库以**只读**方式挂载
- 容器内以非 root 用户 `appuser`（uid 1000）运行
- 不使用遥测、不回传任何使用数据
- 报告只写入本地 `reports/` 目录

如果要做到**物理断网**，用模式 B 并给 ollama 预拉模型，然后把出站策略设为全拒绝，功能不受影响。

## 六、排障

| 现象 | 原因 | 处理 |
|---|---|---|
| `API key is required` | `.env` 没被读到 | 确认在 `deploy/` 目录下执行，且 `.env` 存在 |
| 连接模型超时 | 内网出站被拦 | 放行 `LLM_BASE_URL` 的域名/IP；或用模式 B |
| `Access denied: outside the current working directory` | 文件路径不在挂载点内 | 确认 `REVIEW_PATH` 覆盖该文件 |
| 报告为空 | diff 范围无改动 | 换 `-d` 参数，或直接 `-f` 指定文件 |
| 内存不足 | 本地模型太大 | 换 `qwen2.5-coder:7b` |
