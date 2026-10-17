#!/bin/bash
# Run the plugin's tests against a Hermes checkout's test environment.
#   HERMES_SRC=/path/to/hermes-agent tests/run.sh [pytest args]
# The repo root is the plugin package (it has __init__.py), so tests run from a scratch copy
# where the plugin sits in its own folder, the way Hermes installs it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${HERMES_SRC:?set HERMES_SRC to a hermes-agent checkout with a test .venv}"
PY="${HERMES_PYTHON:-$SRC/.venv/bin/python}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/plugin" "$WORK/tests" "$WORK/home/.hermes" "$WORK/tmp" "$WORK/runtime"
cp -r "$HERE"/__init__.py "$HERE"/lbcore.py "$HERE"/plugin.yaml "$HERE"/dashboard "$HERE"/desktop "$WORK/plugin/"
cp "$HERE"/tests/test_*.py "$WORK/tests/"
cd "$WORK"
env -i PATH="$PATH" HOME="$WORK/home" PYTHONPATH="$SRC" HERMES_HOME="$WORK/home/.hermes" \
  CODEX_LB_PLUGIN_DIR="$WORK/plugin" TMPDIR="$WORK/tmp" HERMES_RUNTIME_DIR="$WORK/runtime" TZ=UTC LANG=C.UTF-8 \
  "$PY" -m pytest -q -p no:cacheprovider --rootdir "$WORK" tests "$@"
# Execute the uncompiled desktop module with synthetic SDK and React hook doubles.
# Real Desktop rendering and live-backend acceptance remain separate gates.
env -i PATH="$PATH" HOME="$WORK/home" TMPDIR="$WORK/tmp" \
  node --experimental-vm-modules --test "$HERE/tests/desktop/test_plugin.mjs"
