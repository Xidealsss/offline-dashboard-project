#!/bin/bash
# 一键发布 Mac 应用（PLAN 8.5 R3）：
#   构建 → 冻结包自检 → 同步交付物 → 重打源码快照 → 打印版本与校验和
# 任何一步失败即中止，不会留下半成品——「交付物比 dist 旧一轮」那类事故就是这么来的。
set -euo pipefail
cd "$(dirname "$0")" || exit 1

APP_NAME="看板操作台"
# 构建用 Python 版本（PLAN 8.5 R4）。上一轮从 3.12 漂到 3.14，导致交付物与 dist
# 内嵌的 Python 不是同一个。真正要防的是**静默**漂移，而不是必须用某个版本，
# 所以这里是「首次构建用什么就记进 .build-python，之后变了才拦下来问」。
PIN_FILE=".build-python"

die() { echo ""; echo "❌ $*" >&2; [ -t 0 ] && read -r -p "按回车键退出..."; exit 1; }

VERSION=$(python3 - <<'PY'
import re, pathlib
m = re.search(r'^__version__\s*=\s*"([^"]+)"', pathlib.Path("看板操作台.py").read_text(encoding="utf-8"), re.M)
print(m.group(1) if m else "")
PY
)
[ -n "$VERSION" ] || die "看板操作台.py 里找不到 __version__"
echo "==> 准备打包 $APP_NAME $VERSION"

# ---------------------------------------------------------------- 1. 构建环境
WANT=""
[ -f "$PIN_FILE" ] && WANT="$(tr -d '[:space:]' < "$PIN_FILE")"

PY=""
if [ -n "$WANT" ]; then                      # 记录过版本：优先找同一个
  for CAND in \
    "/opt/homebrew/bin/python$WANT" \
    "/usr/local/bin/python$WANT" \
    "/Library/Frameworks/Python.framework/Versions/$WANT/bin/python$WANT" \
    "$(command -v "python$WANT" 2>/dev/null)" ; do
    if [ -n "$CAND" ] && [ -x "$CAND" ]; then PY="$CAND"; break; fi
  done
fi
if [ -z "$PY" ]; then
  PY="$(command -v python3 2>/dev/null || true)"
  [ -n "$PY" ] || die "没找到 python3，请先安装：https://www.python.org/downloads/"
fi
HAVE="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

if [ -n "$WANT" ] && [ "$HAVE" != "$WANT" ]; then
  echo "⚠️  上次是用 Python $WANT 构建的，这次只找得到 $HAVE。"
  echo "   换版本本身没问题，但内嵌的 Python 会跟着变，产物和上一版就不是一回事了。"
  read -r -p "用 $HAVE 继续并把它记为新的构建版本？[y/N] " ans
  case "$ans" in [Yy]*) ;; *) die "已中止。想沿用旧版本请先装上 python$WANT" ;; esac
fi
echo "$HAVE" > "$PIN_FILE"
echo "==> 构建用 Python: $("$PY" -V 2>&1)  ($PY)"

if [ "$(uname -m)" != "x86_64" ]; then
  echo "==> 注意：本机是 $(uname -m)，产出的 app 只能在 Apple 芯片 Mac 上运行。"
  echo "    Intel Mac 双击会直接闪退且没有任何提示（PLAN 8.5 M4）。"
fi

if [ ! -x ".build-venv/bin/python" ]; then
  echo "==> 创建 .build-venv ..."
  "$PY" -m venv .build-venv || die "创建虚拟环境失败"
fi
VENV_PY_VER=$(.build-venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")
if [ "$VENV_PY_VER" != "$HAVE" ]; then
  echo "==> .build-venv 是 Python $VENV_PY_VER，与本次的 $HAVE 不符，重建 ..."
  rm -rf .build-venv
  "$PY" -m venv .build-venv || die "重建虚拟环境失败"
fi
.build-venv/bin/python -m pip install --upgrade pip pyinstaller openpyxl >/dev/null \
  || die "安装打包依赖失败"

# ---------------------------------------------------------------- 2. 构建
echo "==> 打包中 ..."
rm -rf "dist/$APP_NAME.app" "dist/$APP_NAME"
.build-venv/bin/pyinstaller "$APP_NAME.spec" --noconfirm --clean >/dev/null \
  || die "PyInstaller 打包失败"
[ -d "dist/$APP_NAME.app" ] || die "没有产出 dist/$APP_NAME.app"

# ---------------------------------------------------------------- 3. 冻结包自检
echo "==> 冻结包自检 ..."
"dist/$APP_NAME.app/Contents/MacOS/$APP_NAME" --smoke-test || die "冻结包自检未通过，产物不可发布"

# ---------------------------------------------------------------- 4. 打分发 zip
echo "==> 打分发包 ..."
(
  cd dist
  rm -f "${APP_NAME}_Mac.zip"
  zip -r -y -q "${APP_NAME}_Mac.zip" "$APP_NAME.app"
) || die "打 zip 失败"

# PyInstaller 会同时留下 onedir 目录和 .app，两者内容一样；build/ 是中间产物。
# 加起来 30MB 上下，留着只是占地方。
echo "==> 清理中间产物 ..."
rm -rf build "dist/$APP_NAME"

# ---------------------------------------------------------------- 5. 同步交付物
DELIVER="../交付物"
if [ -d "$DELIVER" ]; then
  echo "==> 同步交付物 ..."
  rm -rf "$DELIVER/$APP_NAME.app"
  cp -R "dist/$APP_NAME.app" "$DELIVER/$APP_NAME.app"
  cp "dist/${APP_NAME}_Mac.zip" "$DELIVER/${APP_NAME}_Mac.zip"
  cat > "$DELIVER/请先阅读.txt" <<EOF
项目看板操作台 $VERSION

· 仅支持 Apple 芯片（M 系列）Mac。Intel Mac 双击会直接闪退。
· 首次运行若提示「无法验证开发者」：右键点 $APP_NAME.app → 选「打开」→ 再点「打开」。
· 应用不在 Dock 里显示图标，界面在浏览器中。关掉浏览器标签后会自动退出。
· 详细说明见 使用说明.md。
EOF
else
  echo "==> 未找到 $DELIVER，跳过交付物同步"
fi

# ---------------------------------------------------------------- 6. 重打源码快照
# 注意：这里**不能**写成「快照存在才重打」。那样一旦快照丢过一次就再也回不来，
# 而且第 7 步的校验和会跟着静默少一行——2026-08-08 就这么丢了一次。
SRC_DIR="$(basename "$PWD")"
SNAPSHOT="../${SRC_DIR}.zip"
echo "==> 重打源码快照 ..."
rm -f "$SNAPSHOT"
(
  cd ..
  zip -r -q "${SRC_DIR}.zip" "$SRC_DIR" \
    -x "*/dist/*" "*/build/*" "*/.build-venv/*" "*/_to_delete/*" \
       "*/.DS_Store" "*/__pycache__/*" "*.zip"
) || die "重打快照失败"
[ -s "$SNAPSHOT" ] || die "快照生成了但是空的：$SNAPSHOT"

# ---------------------------------------------------------------- 7. 校验和
echo ""
echo "======================================================"
echo "✅ 发布完成：$APP_NAME $VERSION"
echo "------------------------------------------------------"
MISSING=0
for f in "dist/${APP_NAME}_Mac.zip" "$DELIVER/${APP_NAME}_Mac.zip" "$SNAPSHOT"; do
  if [ -s "$f" ]; then
    printf "%s  %10d  %s\n" "$(shasum -a 256 "$f" | cut -c1-16)" "$(wc -c < "$f")" "$f"
  else
    printf "%-16s  %10s  %s\n" "!! 缺失或为空" "-" "$f"     # 少一行比错一行更难发现
    MISSING=1
  fi
done
echo "------------------------------------------------------"
if [ "$MISSING" = "1" ]; then
  echo "⚠️  上面有产物缺失，别把这次结果当成完整发布。"
else
  echo "前两行校验和必须一致（交付物 = dist）。记进 变更记录.md。"
fi
echo "app 体积：$(du -sh "dist/$APP_NAME.app" | cut -f1)"
echo "======================================================"
[ -t 0 ] && read -r -p "按回车键退出..."
exit 0
