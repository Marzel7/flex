#!/bin/bash
# Wrapper script to ensure algotrader environment is activated

# Initialize conda
eval "$(conda shell.bash hook)"

# Activate algotrader environment
conda activate algotrader

# Disable Python bytecode caching to ensure fresh code is always loaded
export PYTHONDONTWRITEBYTECODE=1

# Run the listener
python -B pumpfun_curve_listener.py
