#!/bin/bash
# 一键运行看板自检（解析 / 校验 / 渲染 / 浏览器回归）
cd "$(dirname "$0")" || exit 1

# 与 运行看板.command 用同一套 Python 探测顺序
PY=""
for CAND in \
  "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" \
  "$(command -v python3 2>/dev/null)" \
  /usr/bin/python3 ; do
  [ -n "$CAND" ] && [ -x "$CAND" ] && PY="$CAND" && break
done
if [ -z "$PY" ]; then
  echo "未找到 Python，请先安装 Python 3 后重试。"
  read -r -p "按回车键退出..."
  exit 1
fi

"$PY" -c "import openpyxl" 2>/dev/null || {
  echo "缺少 openpyxl，请先运行一次「运行看板.command」完成安装。"
  read -r -p "按回车键退出..."
  exit 1
}

echo "使用 Python: $PY"
"$PY" -c "import playwright" 2>/dev/null \
  && echo "已检测到 playwright，将一并运行浏览器回归。" \
  || echo "未安装 playwright，浏览器回归会自动跳过（如需启用：$PY -m pip install playwright && $PY -m playwright install chromium）。"
echo ""

"$PY" -m unittest discover -s tests -t tests -v
CODE=$?

echo ""
if [ $CODE -eq 0 ]; then
  echo "✅ 自检通过。"
else
  echo "❌ 自检未通过，请查看上面的失败项。"
fi
read -r -p "按回车键退出..."
exit $CODE
