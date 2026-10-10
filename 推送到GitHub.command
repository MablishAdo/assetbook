#!/bin/bash
# AssetBook · 一键推送到 GitHub
# 双击运行；第一次会问你仓库地址，之后直接回车沿用。

cd "$(dirname "$0")" || exit 1

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1"; }
die()  { printf "${RED}✗${NC} %s\n" "$1"; }

echo ""
echo "  ─────────────────────────────────────"
echo "   AssetBook · 推送到 GitHub"
echo "  ─────────────────────────────────────"
echo ""

# 0) git 在不在
if ! command -v git >/dev/null 2>&1; then
  die "没找到 git。先装：xcode-select --install"
  exit 1
fi

# 1) 仓库地址
REMOTE_FILE=".git/assetbook-remote"
DEFAULT=""
[ -f "$REMOTE_FILE" ] && DEFAULT="$(cat "$REMOTE_FILE")"
printf "GitHub 仓库地址 [${DEFAULT:-https://github.com/你的用户名/assetbook.git}]: "
read -r REPO
REPO="${REPO:-$DEFAULT}"
[ -z "$REPO" ] && { die "没填仓库地址，退出。"; exit 1; }
echo "$REPO" > "$REMOTE_FILE"

# 2) 还没建仓库？给提示
case "$REPO" in
  https://github.com/*|git@github.com:*) : ;;
  *) warn "地址看起来不像 GitHub 仓库（要以 https://github.com/... 或 git@github.com:... 开头）" ;;
esac

# 3) 有改动先提交
if [ -n "$(git status --porcelain)" ]; then
  warn "发现未提交的改动："
  git status --short | sed 's/^/    /'
  printf "输入本次提交说明 [更新账本]: "
  read -r MSG
  MSG="${MSG:-更新账本}"
  git add -A
  git commit -m "$MSG" || die "提交失败"
  say "已提交：$MSG"
fi

# 4) 挂远程并推送
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO"
else
  git remote add origin "$REPO"
fi
say "远程：$REPO"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo ""
if git push -u origin "$BRANCH" 2>&1; then
  echo ""
  say "推送成功！浏览器打开："
  say "${REPO%.git}"
  echo ""
  echo "  下一步（服务器上）："
  echo "   · git clone ${REPO%.git}.git && cd assetbook"
  echo "   · cp .env.example .env   # 改掉 ASSETBOOK_PASSWORD"
  echo "   · docker compose up -d --build"
  echo "   · docker compose exec assetbook python /app/setup-1000.py   # 一键配好账户与预算"
  echo ""
else
  echo ""
  die "推送失败。常见原因："
  echo "    1) 仓库还没建 → 去 https://github.com/new 建一个（不要勾选 README/.gitignore/License）"
  echo "    2) 没登录或没权限 → 终端里运行：git config --global credential.helper osxkeychain"
  echo "       然后重新双击本脚本，弹窗里用浏览器登录 GitHub 即可"
  echo "    3) 想用 SSH → 先配好 key，再把地址换成 git@github.com:用户名/assetbook.git"
fi

# 5) 留住窗口
echo ""
printf "按回车关闭…"
read -r
