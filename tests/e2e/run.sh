#!/bin/bash
# Run the e2e test inside a Hermes checkout (copied in, then removed).
#   HERMES_SRC=/path/to/hermes-agent tests/e2e/run.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="${HERMES_SRC:?set HERMES_SRC to a hermes-agent checkout with a test .venv}"
DEST="$SRC/tests/e2e/core/providers"
cleanup() { rm -rf "$DEST/codex_lb_plugin_src" "$DEST/test_zz_codexlb_plugin_e2e.py"; }
trap cleanup EXIT
mkdir -p "$DEST/codex_lb_plugin_src"
cp -r "$HERE"/__init__.py "$HERE"/lbcore.py "$HERE"/plugin.yaml "$HERE"/dashboard "$HERE"/desktop "$DEST/codex_lb_plugin_src/"
cp "$HERE/tests/e2e/test_zz_codexlb_plugin_e2e.py" "$DEST/"
cd "$SRC"
scripts/run_tests.sh --include-integration tests/e2e/core/providers/test_zz_codexlb_plugin_e2e.py "$@"
