#!/bin/bash
# ============================================================
# 飞书 webhook 通知脚本
# 用法：./feishu-notify.sh <标题> <内容> [类型]
# 类型：info / success / warning / error（对应不同颜色）
# ============================================================

set -e

# 飞书机器人 webhook 地址（从环境变量读，不硬编码）
FEISHU_WEBHOOK="${FEISHU_WEBHOOK:-}"

if [ -z "$FEISHU_WEBHOOK" ]; then
    echo "❌ 请设置环境变量 FEISHU_WEBHOOK"
    exit 1
fi

TITLE="${1:-通知}"
CONTENT="${2:-}"
TYPE="${3:-info}"

# 不同类型对应不同 emoji 和颜色
case "$TYPE" in
    success)
        PREFIX="✅"
        ;;
    warning)
        PREFIX="⚠️"
        ;;
    error)
        PREFIX="❌"
        ;;
    info)
        PREFIX="🔔"
        ;;
    *)
        PREFIX="📢"
        ;;
esac

# 构建富文本消息（飞书 post 格式，支持换行和格式）
MESSAGE="${PREFIX} 【${TITLE}】
${CONTENT}"

# 发送到飞书
curl -s -X POST "$FEISHU_WEBHOOK" \
    -H "Content-Type: application/json" \
    -d "$(cat <<EOF
{
    "msg_type": "text",
    "content": {
        "text": "${MESSAGE}"
    }
}
EOF
)"

echo ""
echo "✅ 飞书通知已发送"
