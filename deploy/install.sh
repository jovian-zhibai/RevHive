#!/usr/bin/env bash
# RevHive 私有化部署自检脚本
# 用法：bash install.sh [你的仓库路径]
set -euo pipefail

cd "$(dirname "$0")"

GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[0;33m'; NC=$'\033[0m'
ok()   { echo "${GREEN}✓${NC} $1"; }
warn() { echo "${YELLOW}!${NC} $1"; }
die()  { echo "${RED}✗${NC} $1" >&2; exit 1; }

echo "RevHive 私有化部署自检"
echo "────────────────────────────────"

# 1. 依赖
command -v docker >/dev/null 2>&1 || die "未安装 Docker。请先安装 Docker Desktop 或 Docker Engine。"
ok "docker 已安装：$(docker --version | cut -d, -f1)"

docker info >/dev/null 2>&1 || die "Docker 守护进程未运行，请先启动 Docker。"
ok "Docker 守护进程运行中"

docker compose version >/dev/null 2>&1 || die "缺少 docker compose 插件（需要 v2）。"
ok "docker compose 可用"

# 2. 配置
if [ ! -f .env ]; then
  cp .env.example .env
  warn "已生成 .env，请填入 LLM_API_KEY 和 LLM_BASE_URL 后重新运行本脚本"
  exit 0
fi
ok ".env 已存在"

# shellcheck disable=SC1091
set -a; . ./.env; set +a

[ -n "${LLM_BASE_URL:-}" ] || die ".env 中缺少 LLM_BASE_URL"
[ -n "${LLM_API_KEY:-}" ]  || die ".env 中缺少 LLM_API_KEY"

if [ "${LLM_API_KEY}" = "sk-your-key-here" ]; then
  die "LLM_API_KEY 还是占位值，请填入真实密钥"
fi
ok "模型配置：${LLM_MODEL:-未设置} @ ${LLM_BASE_URL}"

# 3. 待审查仓库
if [ $# -ge 1 ]; then
  REVIEW_PATH="$(cd "$1" && pwd)"
  if grep -q '^REVIEW_PATH=' .env; then
    sed -i.bak "s|^REVIEW_PATH=.*|REVIEW_PATH=${REVIEW_PATH}|" .env && rm -f .env.bak
  else
    echo "REVIEW_PATH=${REVIEW_PATH}" >> .env
  fi
  ok "已设置待审查仓库：${REVIEW_PATH}"
fi

[ -d "${REVIEW_PATH:-}" ] || die "REVIEW_PATH 不是一个目录：${REVIEW_PATH:-未设置}"
ok "仓库目录存在"

# 4. 构建
echo
echo "构建镜像（首次约 1-3 分钟）..."
docker compose build --quiet
ok "镜像构建完成"

# 5. 自检
echo
echo "运行离线自检（demo 模式，不消耗 token）..."
docker compose run --rm --no-deps revhive demo --format markdown -o /reports/selftest.md
if [ -f reports/selftest.md ]; then
  ok "自检通过，报告已生成：deploy/reports/selftest.md"
else
  die "自检失败：未生成报告"
fi

echo
echo "────────────────────────────────"
echo "${GREEN}部署完成${NC}"
echo
echo "审查一个文件："
echo "  docker compose run --rm revhive review -f src/main.py --format markdown -o /reports/main.md"
echo
echo "审查最近的改动："
echo "  docker compose run --rm revhive review -d HEAD~1 --format markdown -o /reports/diff.md"
echo
echo "完全离线（模型跑在本机）："
echo "  docker compose -f docker-compose.yml -f docker-compose.ollama.yml up -d ollama"
echo
