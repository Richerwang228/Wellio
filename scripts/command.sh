#!/bin/bash
set -u
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
PYTHON="$ROOT/backend/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "找不到成品目录内的 Python：$PYTHON"
  echo "请检查 backend/.venv 是否复制完整。"
  RESULT=1
else
  "$PYTHON" "$ROOT/scripts/manage.py" "$@"
  RESULT=$?
fi
if [ "$RESULT" -ne 0 ] && [ -t 0 ]; then
  echo "按回车关闭此窗口。"
  read -r _
fi
exit "$RESULT"
