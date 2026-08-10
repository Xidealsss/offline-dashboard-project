#!/bin/bash
# 一键从 Excel 模板生成离线项目看板并打开
cd "$(dirname "$0")" || exit 1

# 按序探测可用的 Python：不再写死任何个人绝对路径，换台机器/换用户名也能跑
PY=""
for CAND in \
  "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" \
  "$(command -v python3 2>/dev/null)" \
  /opt/homebrew/bin/python3 \
  /usr/local/bin/python3 \
  /usr/bin/python3 ; do
  [ -n "$CAND" ] && [ -x "$CAND" ] && PY="$CAND" && break
done
if [ -z "$PY" ]; then
  echo "未找到 Python，请先安装 Python 3 后重试。"
  read -r -p "按回车键退出..."
  exit 1
fi

# 本目录下的 .venv 优先（下面装依赖失败时会创建它）
[ -x ".venv/bin/python3" ] && PY="$PWD/.venv/bin/python3"

if ! "$PY" -c "import openpyxl" >/dev/null 2>&1; then
  echo "缺少 openpyxl 库，正在安装（仅首次需要）..."
  if ! "$PY" -m pip install --user openpyxl; then
    # 新版 macOS / Homebrew 的 Python 受 PEP 668 保护，--user 会被拒绝，改用本地虚拟环境
    echo ""
    echo "直接安装被系统拒绝（PEP 668 外部管理环境），改为在本目录创建 .venv ..."
    if "$PY" -m venv .venv && .venv/bin/python3 -m pip install --upgrade pip openpyxl; then
      PY="$PWD/.venv/bin/python3"
    else
      echo ""
      echo "自动安装失败。请手动执行下面任一条后重试："
      echo "  $PY -m pip install --break-system-packages openpyxl"
      echo "  $PY -m venv .venv && .venv/bin/pip install openpyxl"
      read -r -p "按回车键退出..."
      exit 1
    fi
  fi
fi

echo "使用 Python: $PY"
"$PY" build_dashboard.py --input 家电产品项目_任务数据.xlsx --output 项目看板.html
CODE=$?

if [ $CODE -eq 1 ]; then
  echo ""
  echo "表格数据有问题，看板未生成。按上面每条提示定位到具体行修改后重试。"
  read -r -p "按回车键退出..."
  exit 1
elif [ $CODE -ne 0 ]; then
  echo ""
  echo "环境或读写出错（退出码 $CODE），看板未生成。"
  read -r -p "按回车键退出..."
  exit $CODE
fi

open "项目看板.html"
echo "看板已生成并打开。"
