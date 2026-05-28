#!/bin/bash
# Usage:
#   bash tests/run_tests.sh           # all suites
#   bash tests/run_tests.sh setup     # minikube smoke tests
#   bash tests/run_tests.sh installer # installer smoke tests
#   bash tests/run_tests.sh core      # Python unit + UI interface tests (no cluster needed)

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUITE="${1:-all}"

run_setup() {
    echo ""
    echo "══════════════════════════════════════"
    echo "  Suite: setup (Minikube)"
    echo "══════════════════════════════════════"
    bash "$SCRIPT_DIR/setup/test_minikube.sh"
}

run_installer() {
    echo ""
    echo "══════════════════════════════════════"
    echo "  Suite: installer"
    echo "══════════════════════════════════════"
    bash "$SCRIPT_DIR/installer/test_install.sh"
}

run_core() {
    echo ""
    echo "══════════════════════════════════════"
    echo "  Suite: core (pytest)"
    echo "══════════════════════════════════════"
    cd "$SCRIPT_DIR/.."
    core/.venv/bin/pytest tests/core/ -v
}

case "$SUITE" in
    setup)     run_setup ;;
    installer) run_installer ;;
    core)      run_core ;;
    all)       run_setup; run_installer; run_core ;;
    *)
        echo "Unknown suite: $SUITE"
        echo "Usage: $0 [all|setup|installer|core]"
        exit 1
        ;;
esac

echo ""
echo "✅ All tests passed"
