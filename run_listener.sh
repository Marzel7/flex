#!/bin/bash
# Run listener in foreground with unbuffered output
cd "$(dirname "$0")"
exec python -u -m src.core.pumpfun_curve_listener
