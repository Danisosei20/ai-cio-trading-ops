import sys
sys.path.insert(0, '.')
from robinhood_tools.regime_detector import RegimeDetector, Regime

det = RegimeDetector()

# Test BULL_TREND
r = det.classify(close=150.0, vwap_slope=0.15, adx=28.0, atr_percentile=60.0, breadth=0.65)
print(f'BULL_TREND test: regime={r.regime.value}, reasons={r.reasons}')
assert r.regime == Regime.BULL_TREND

# Test BEAR_TREND
r = det.classify(close=100.0, vwap_slope=-0.15, adx=28.0, atr_percentile=60.0, breadth=0.35)
print(f'BEAR_TREND test: regime={r.regime.value}, reasons={r.reasons}')
assert r.regime == Regime.BEAR_TREND

# Test HIGH_VOL
r = det.classify(close=150.0, vwap_slope=0.15, adx=28.0, atr_percentile=85.0, breadth=0.65)
print(f'HIGH_VOL test: regime={r.regime.value}, reasons={r.reasons}')
assert r.regime == Regime.HIGH_VOL

# Test CHOP
r = det.classify(close=150.0, vwap_slope=0.01, adx=15.0, atr_percentile=30.0, breadth=0.50)
print(f'CHOP test: regime={r.regime.value}, reasons={r.reasons}')
assert r.regime == Regime.CHOP

print()
print('All regime detector tests passed!')
