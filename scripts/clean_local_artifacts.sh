#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT"

rm -rf \
  .pytest_cache \
  __pycache__ \
  app/__pycache__ \
  brain/__pycache__ \
  config/__pycache__ \
  invest/__pycache__ \
  market_data/__pycache__ \
  runtime/__pycache__ \
  strategies/__pycache__ \
  tests/__pycache__

find . -type d -name '__pycache__' -prune -exec rm -rf {} +
find . -name '.DS_Store' -delete

printf 'Cleaned local artifacts in %s\n' "$ROOT"
