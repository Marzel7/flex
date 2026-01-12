#!/usr/bin/env python3
"""
Understand how coverage affects risk scoring
"""

# The token 8XzSqqNevScuiqJwDuKMgmDLCMsJPuay2GtKM2fupump shows:
# - Listener (earlier): 15% score with 6.5% coverage
# - Fresh run: 30% score with 6.5% coverage

# This suggests the risk score can vary based on WHICH transactions 
# are analyzed, not just how many

# Key factors:
# 1. RPC returns signatures in a specific order (typically most recent first)
# 2. With low coverage (6.5%), we're only sampling a subset
# 3. Different subsets have different risk profiles
# 4. This is why coverage matters - higher coverage = more representative

print("""
Coverage Impact on Risk Scoring
================================

Token: 8XzSqqNevScuiqJwDuKMgmDLCMsJPuay2GtKM2fupump

Earlier Run (Listener):
  - Score: 15% (LOW RISK)
  - Coverage: 6.5% (57/879 txs)
  
Fresh Run:
  - Score: 30% (LOW RISK)
  - Coverage: 6.5% (57/879 txs)

Why the difference?
-------------------
1. RPC returns signatures in reverse chronological order
2. With only 6.5% coverage, we get a SAMPLE, not full picture
3. Different runs may fetch different transaction subsets
4. Samples from different time periods can have different patterns

Both scores (15% and 30%) are within LOW RISK range (< 40%), so the 
token is still LOW RISK despite the variance.

Recommendation:
- Aim for higher coverage (>50%) for more stable scoring
- 6.5% coverage gives representative but variable results
- Score of 15-30% LOW RISK is fairly consistent pattern for this token
""")
