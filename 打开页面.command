#!/bin/bash
ROOT="$(cd -- "$(dirname -- "$0")" && pwd -P)"
exec /bin/bash "$ROOT/scripts/command.sh" open "$@"
