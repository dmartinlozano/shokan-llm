#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/installer/utils.sh"
backup_data "shokanllm" "$SCRIPT_DIR"
