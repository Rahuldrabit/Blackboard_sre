#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 2 ]]; then
    echo 'Usage: bash scripts/run_sregym_lite.sh /path/to/SREGym qwen|deepseek [--dry-run] [--problem ID] [--stages diagnosis]' >&2
    exit 2
fi
sregym_checkout=$1
selected_model=$2
shift 2
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec uv run --project "$repo_root" python "$repo_root/scripts/run_campaign.py" \
    --sregym "$sregym_checkout" --models "$selected_model" "$@"
