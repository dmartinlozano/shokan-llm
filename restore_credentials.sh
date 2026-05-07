#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/installer/utils.sh"
restore_credentials "shokanllm" "$SCRIPT_DIR/credentials-backup.age"
