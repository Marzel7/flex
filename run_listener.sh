#!/bin/bash
# Wrapper script to ensure algotrader environment is activated

# Initialize conda
eval "$(conda shell.bash hook)"

# Activate algotrader environment
conda activate algotrader

# Run the listener
python pumpfun_curve_listener.py
