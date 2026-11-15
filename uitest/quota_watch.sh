#!/bin/bash
# quota_watch.sh —— 配额恢复看门狗
# 用法: bash quota_watch.sh "2026-10-24 12:55:54" [标签]
# 行为: 睡到指定本地时刻(配额重置点)后退出。退出即触发主会话的任务完成通知,
#       主会话据此自动 resume 因 429 配额限停的 agent。
# 原理: 不轮询不探测，纯定时唤醒——失败通知本身已含重置时间戳，无需猜测。
TARGET="$1"
LABEL="${2:-quota}"
[ -z "$TARGET" ] && { echo "usage: quota_watch.sh \"YYYY-MM-DD HH:MM:SS\" [label]"; exit 1; }
target_epoch=$(date -d "$TARGET" +%s 2>/dev/null)
[ -z "$target_epoch" ] && { echo "bad target time: $TARGET"; exit 1; }
now=$(date +%s)
wait_s=$(( target_epoch - now + 30 ))   # +30s 缓冲，避免卡在重置边界
if [ "$wait_s" -le 0 ]; then
  echo "$LABEL: 重置时刻 $TARGET 已过，立即返回"
  exit 0
fi
echo "$LABEL: 等待 ${wait_s}s 至 $TARGET (配额重置)"
sleep "$wait_s"
echo "$LABEL: 已到 $TARGET，配额窗口应已重置——唤醒主会话执行恢复"
