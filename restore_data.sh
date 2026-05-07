#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/installer/utils.sh"
restore_data "shokanllm" "${1:-}" "$SCRIPT_DIR"
