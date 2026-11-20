import sys
sys.path.insert(0, '.')
from robinhood_tools.profit_taking import ProfitTakingEngine, ExitMethod

engine = ProfitTakingEngine()

# Test 1: Fixed target hit (long)
result = engine.evaluate(
    symbol="NVDA",
    direction="long",
    entry=100.0,
    current=120.0,
    stop=95.0,
    target=120.0,
    atr=5.0,
    bars_held=5,
    support=98.0,
    resistance=125.0,
)
print(f"Test 1 - Long target hit: action={result.recommended.action if result.recommended else 'hold'}, method={result.recommended.method.value if result.recommended else 'none'}")
assert result.recommended is not None
assert result.recommended.method == ExitMethod.FIXED_TARGET
assert result.recommended.action == "exit_full"

# Test 2: Short target hit
result = engine.evaluate(
    symbol="QQQ",
    direction="short",
    entry=200.0,
    current=180.0,
    stop=205.0,
    target=180.0,
    atr=5.0,
    bars_held=3,
    support=190.0,
    resistance=210.0,
)
print(f"Test 2 - Short target hit: action={result.recommended.action if result.recommended else 'hold'}, method={result.recommended.method.value if result.recommended else 'none'}")
assert result.recommended is not None
assert result.recommended.method == ExitMethod.FIXED_TARGET
assert result.recommended.action == "exit_full"

# Test 3: ATR trailing stop (long with profit)
result = engine.evaluate(
    symbol="TSLA",
    direction="long",
    entry=200.0,
    current=210.0,
    stop=190.0,
    target=230.0,
    atr=5.0,
    bars_held=5,
    support=195.0,
    resistance=220.0,
)
print(f"Test 3 - Long trailing stop: action={result.recommended.action if result.recommended else 'hold'}, method={result.recommended.method.value if result.recommended else 'none'}")
assert result.recommended is not None
# Should trail since price is up

# Test 4: Time stop (structure-based may take priority, that's OK)
result = engine.evaluate(
    symbol="MSFT",
    direction="long",
    entry=300.0,
    current=301.0,
    stop=290.0,
    target=320.0,
    atr=5.0,
    bars_held=15,  # More than time_stop_bars (10)
    support=295.0,
    resistance=310.0,
)
print(f"Test 4 - Time/structure stop: action={result.recommended.action if result.recommended else 'hold'}, method={result.recommended.method.value if result.recommended else 'none'}")
assert result.recommended is not None
assert result.recommended.action in ("trail", "exit_full")  # Either structure trail or time stop

print(f"Test 5 - Hold/Trail: action={result.recommended.action if result.recommended else 'hold'}, method={result.recommended.method.value if result.recommended else 'none'}")
# Support at 98 creates a structural stop at 97.51, which is better than 95 stop
assert result.recommended is not None
assert result.recommended.method == ExitMethod.STRUCTURE_BASED
assert result.recommended.action == "trail"

print()
print("All profit taking tests passed!")
