"""Frozen official Birdeye OHLC interval contract for the 7-day survivor wave."""
SUPPORTED_INTERVALS=frozenset({'1s','15s','30s','1m','3m','5m','15m','30m','1H','2H','4H','6H','8H','12H','1D','3D','1W','1M'})
EXTENDED_TIERS=(('TIER_5','15m',86400,172800),('TIER_6','30m',172800,259200),('TIER_7','1H',259200,604800))
def validate_interval(interval):
 if interval not in SUPPORTED_INTERVALS: raise ValueError('UNSUPPORTED_BIRDEYE_INTERVAL')
 return interval
def validate_extended_tiers():
 previous=86400
 for _,interval,start,end in EXTENDED_TIERS:
  validate_interval(interval)
  if start!=previous or end<=start: raise ValueError('INVALID_EXTENDED_TIER_BOUNDARY')
  previous=end
 return previous==604800
