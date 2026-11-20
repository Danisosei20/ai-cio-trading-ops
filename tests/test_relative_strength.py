import sys
sys.path.insert(0, '.')
from robinhood_tools.relative_strength import RelativeStrengthScanner

scanner = RelativeStrengthScanner("SPY")

# Mock data: NVDA outperforming SPY
nvda_bars = []
spy_bars = []
for i in range(61):
    # SPY goes up 5% over 60 days
    spy_bars.append({"close": 100 * (1 + 0.05 * i / 60), "volume": 1000000})
    # NVDA goes up 15% over 60 days
    nvda_bars.append({"close": 100 * (1 + 0.15 * i / 60), "volume": 2000000})

results = scanner.scan(
    tickers=["NVDA"],
    price_data={"NVDA": nvda_bars},
    benchmark_data=spy_bars,
)

print(f"Ticker: {results[0].ticker}")
print(f"RS Score: {results[0].rs_score:.1f}")
print(f"Classification: {results[0].classification}")
print(f"Perf 20d: {results[0].perf_20d:.2f}%")
print(f"Benchmark 20d: {results[0].perf_benchmark_20d:.2f}%")

assert results[0].rs_score > 50, "NVDA should have high RS score vs SPY"
assert results[0].classification == "strong"
print()
print("All relative strength tests passed!")
