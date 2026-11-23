#!/bin/bash
set -euo pipefail

task=${1#task-}
task=${task%.workspaces.internal}
exec {source}<&0
exec {input}< <(printf '%s\n' "$task"; exec cat <&"$source")
writer=$!
trap 'kill "$writer" 2>/dev/null || true; wait "$writer" 2>/dev/null || true' EXIT
nc -N -U /run/platform-peer/connect <&"$input"
