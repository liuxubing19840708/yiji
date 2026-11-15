#!/usr/bin/env bash
# 扫菲 ERP 一键部署（本地 → GitHub → 云服务器）
#
# 用法：
#   git deploy                推送当前分支并触发服务器部署
#   git deploy origin main    透传额外参数给 git push
#
# 前提：本机已配置到 GitHub 的 SSH 密钥、到云服务器的免密 SSH。
# 服务器端 /opt/deploy.sh 负责：pull → 装依赖 → migrate → collectstatic → 重启 → 健康检查。
set -Eeuo pipefail

SERVER="root@8.136.243.92"
REMOTE_DEPLOY="bash /opt/deploy.sh"

# 0. 未提交改动提醒（不阻断，只提示）
if [[ -n "$(git status --porcelain)" ]]; then
  echo "⚠️  本地存在未提交改动，这些内容不会被部署："
  git status -s | head -10
  echo "（如需部署，请先 git add/commit，或按 Ctrl+C 中止）"
  read -r -p "仍继续部署已提交的内容吗？[y/N] " ans
  [[ "${ans:-N}" =~ ^[Yy]$ ]] || { echo "已取消。"; exit 1; }
fi

echo "==> [1/2] 推送本地提交到 GitHub ..."
git push "$@"

echo "==> [2/2] SSH 触发云服务器部署（实时日志如下）..."
ssh -o BatchMode=yes -o ConnectTimeout=15 "$SERVER" "$REMOTE_DEPLOY"

echo "==> ✅ 全流程结束"
