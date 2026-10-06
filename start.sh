#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_ROOT="$APP_ROOT/frontend"
VENV_ROOT="$APP_ROOT/.venv"
VENV_PYTHON="$VENV_ROOT/bin/python"
URL="http://127.0.0.1:8765"
CHECK_ONLY=0
NO_BROWSER=0

for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --no-browser) NO_BROWSER=1 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m启动失败：%s\033[0m\n' "$1" >&2; exit 1; }
hash_file() { shasum -a 256 "$1" | awk '{print $1}'; }
port_open() {
  "$PYTHON_COMMAND" -c 'import socket,sys; s=socket.socket(); s.settimeout(.35); r=s.connect_ex(("127.0.0.1",8765)); s.close(); raise SystemExit(0 if r == 0 else 1)' >/dev/null 2>&1
}
open_browser() {
  if [[ "$NO_BROWSER" -eq 0 ]]; then open "$URL" >/dev/null 2>&1 || true; fi
}

cd "$APP_ROOT"
printf '\033[32m小说续写工作台 · macOS 启动器\033[0m\n'

command -v python3 >/dev/null 2>&1 || fail "未找到 Python 3。请先安装 Python 3.10 或更高版本。"
PYTHON_COMMAND="$(command -v python3)"
PYTHON_VERSION="$($PYTHON_COMMAND -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MINOR="$($PYTHON_COMMAND -c 'import sys; print(sys.version_info.minor)')"
[[ "$($PYTHON_COMMAND -c 'import sys; print(int(sys.version_info >= (3,10)))')" == "1" ]] || fail "当前 Python 为 $PYTHON_VERSION，需要 Python 3.10 或更高版本。"

command -v node >/dev/null 2>&1 || fail "未找到 Node.js。请先安装 Node.js 20 或更高版本。"
command -v npm >/dev/null 2>&1 || fail "未找到 npm，请重新安装 Node.js。"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 20 ]] || fail "当前 Node.js 版本过低，需要 Node.js 20 或更高版本。"

REQUIREMENTS_HASH="$(hash_file "$APP_ROOT/requirements.txt")"
REQUIREMENTS_STAMP="$VENV_ROOT/.requirements.sha256"
NEEDS_PYTHON=0
[[ -x "$VENV_PYTHON" ]] || NEEDS_PYTHON=1
[[ -f "$REQUIREMENTS_STAMP" && "$(tr -d '\r\n' < "$REQUIREMENTS_STAMP")" == "$REQUIREMENTS_HASH" ]] || NEEDS_PYTHON=1

FRONTEND_HASH="$(hash_file "$FRONTEND_ROOT/package-lock.json")"
FRONTEND_STAMP="$VENV_ROOT/.frontend-lock.sha256"
NEEDS_NPM=0
[[ -d "$FRONTEND_ROOT/node_modules" ]] || NEEDS_NPM=1
[[ -f "$FRONTEND_STAMP" && "$(tr -d '\r\n' < "$FRONTEND_STAMP")" == "$FRONTEND_HASH" ]] || NEEDS_NPM=1

DIST_INDEX="$FRONTEND_ROOT/dist/index.html"
NEEDS_BUILD=$NEEDS_NPM
[[ -f "$DIST_INDEX" ]] || NEEDS_BUILD=1
if [[ "$NEEDS_BUILD" -eq 0 ]] && find "$FRONTEND_ROOT/src" "$FRONTEND_ROOT/index.html" "$FRONTEND_ROOT/vite.config.ts" "$FRONTEND_ROOT/package.json" "$FRONTEND_ROOT/package-lock.json" -type f -newer "$DIST_INDEX" -print -quit | grep -q .; then
  NEEDS_BUILD=1
fi

echo "Python: $PYTHON_VERSION · Node.js: $(node --version)"
echo "Python 依赖：$([[ "$NEEDS_PYTHON" -eq 1 ]] && echo 需要准备 || echo 已就绪)"
echo "前端依赖：$([[ "$NEEDS_NPM" -eq 1 ]] && echo 需要准备 || echo 已就绪)"
echo "前端构建：$([[ "$NEEDS_BUILD" -eq 1 ]] && echo 需要构建 || echo 已是最新)"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  if port_open; then echo "端口 8765：已有服务正在监听"; else echo "端口 8765：可用"; fi
  printf '\n环境检查完成；未安装依赖，也未启动服务。\n'
  exit 0
fi

if port_open; then
  if curl --silent --fail --max-time 2 "$URL/api/projects" >/dev/null 2>&1; then
    printf '\n工作台已经在运行：%s\n' "$URL"
    open_browser
    exit 0
  fi
  fail "端口 8765 已被其他程序占用。请关闭占用程序后重试。"
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  step "创建 Python 虚拟环境"
  "$PYTHON_COMMAND" -m venv "$VENV_ROOT" || fail "无法创建 .venv 虚拟环境。"
fi

if [[ "$NEEDS_PYTHON" -eq 1 ]]; then
  step "安装 Python 依赖（首次运行可能需要几分钟）"
  "$VENV_PYTHON" -m pip install -r "$APP_ROOT/requirements.txt" || fail "Python 依赖安装失败，请检查网络和上方错误信息。"
  printf '%s\n' "$REQUIREMENTS_HASH" > "$REQUIREMENTS_STAMP"
fi

if [[ "$NEEDS_NPM" -eq 1 ]]; then
  step "安装前端依赖（首次运行可能需要几分钟）"
  npm ci --prefix "$FRONTEND_ROOT" || fail "前端依赖安装失败，请检查网络和上方错误信息。"
  printf '%s\n' "$FRONTEND_HASH" > "$FRONTEND_STAMP"
fi

if [[ "$NEEDS_BUILD" -eq 1 ]]; then
  step "构建 Web 界面"
  npm run build --prefix "$FRONTEND_ROOT" || fail "前端构建失败，请检查上方错误信息。"
fi

if [[ "$NO_BROWSER" -eq 0 ]]; then
  (
    for _ in $(seq 1 40); do
      if curl --silent --fail --max-time 1 "$URL" >/dev/null 2>&1; then open_browser; exit 0; fi
      sleep 0.25
    done
  ) &
fi

step "启动工作台"
echo "地址：$URL"
echo "按 Control+C 可停止服务。"
exec "$VENV_PYTHON" "$APP_ROOT/web_main.py"
