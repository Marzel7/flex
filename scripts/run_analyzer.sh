#!/bin/bash

# Run cross-funding network analyzer
# Uses reorganized src/ structure

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"
python3 -m src.analysis.cross_funding_network_analyzer 2>&1
