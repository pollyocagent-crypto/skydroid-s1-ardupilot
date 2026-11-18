#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec "${S1_PYTHON:-.venv/bin/python}" -u bench_s1.py "$@"
