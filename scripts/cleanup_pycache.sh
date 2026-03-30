#!/usr/bin/env bash
# Recursively remove __pycache__ directories and .pyc files from the repo root.
set -euo pipefail

echo "Cleaning __pycache__ directories and .pyc files..."
find . -type d -name '__pycache__' -prune -exec rm -rf '{}' + || true
find . -type f -name '*.pyc' -delete || true
echo "Done."
