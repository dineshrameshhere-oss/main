"""
Full audit + fix of scheduler.py:
1. Remove stale check_rv_gate call (lines 276-281)
2. Fix imports: remove unused, add fetch_finnifty_direction
3. Remove PCR_APPLY_AFTER_HOUR, SUPERTREND_PERIOD, SUPERTREND_MULT from config import
"""
import pathlib, ast, sys
sys.stdout.reconfigure(encoding='utf-8')

sp = pathlib.Path('algo_trading/scheduler.py')
sch = sp.read_text(encoding='utf-8')

# ── 1. Fix config import line ─────────────────────────────────────────────────
old_cfg = ('from .config import (\n'
           '    RATING_AFTERNOON_RELAXED, AFTERNOON_HOUR, AFTERNOON_MIN,'
           'TIME_PRE_MARKET, TIME_MARKET_OPEN, TIME_EOD_CHECK,\n'
           '                     RATING_STRONG_BUY, RATING_STRONG_SELL,\n'
           '                     SUPERTREND_PERIOD, SUPERTREND_MULT, RSI_PERIOD,\n'
           '                     PCR_APPLY_AFTER_HOUR)')
new_cfg = ('from .config import (\n'
           '    RATING_AFTERNOON_RELAXED, AFTERNOON_HOUR, AFTERNOON_MIN,\n'
           '    TIME_PRE_MARKET, TIME_MARKET_OPEN, TIME_EOD_CHECK,\n'
           '    RATING_STRONG_BUY, RATING_STRONG_SELL,\n'
           '    SUPERTREND_PERIOD, SUPERTREND_MULT, RSI_PERIOD)')

if old_cfg in sch:
    sch = sch.replace(old_cfg, new_cfg)
    print('OK: config import cleaned')
else:
    print('WARN: config import pattern not matched — trying partial fix')
    sch = sch.replace(', PCR_APPLY_AFTER_HOUR)', ')')
    sch = sch.replace(',PCR_APPLY_AFTER_HOUR)', ')')
    sch = sch.replace('PCR_APPLY_AFTER_HOUR,', '')
    print('  -> removed PCR_APPLY_AFTER_HOUR inline')

# ── 2. Fix indicators import — keep compute_supertrend for _get_enriched_df ────
# (compute_supertrend IS still used to build df columns in _get_enriched_df)
# Just make sure check_rv_gate is not imported (it was already removed)
if 'check_rv_gate' in sch:
    sch = sch.replace(', check_rv_gate', '')
    sch = sch.replace(',check_rv_gate', '')
    print('OK: check_rv_gate removed from imports')
else:
    print('OK: check_rv_gate not in imports (already clean)')

# ── 3. Add fetch_finnifty_direction to market_data import ─────────────────────
old_mkt = ('from .market_data import (fetch_historical_ohlcv, compress_ohlcv_to_string,\n'
           '                           fetch_first_30min_candle, fetch_intraday_data)')
new_mkt = ('from .market_data import (fetch_historical_ohlcv, compress_ohlcv_to_string,\n'
           '                           fetch_first_30min_candle, fetch_intraday_data,\n'
           '                           fetch_finnifty_direction)')
if old_mkt in sch:
    sch = sch.replace(old_mkt, new_mkt)
    print('OK: fetch_finnifty_direction added to market_data import')
elif 'fetch_finnifty_direction' in sch:
    print('OK: fetch_finnifty_direction already imported')
else:
    # Try to add it inline
    sch = sch.replace(
        'from .market_data import get_auth_headers, fetch_finnifty_direction',
        'from .market_data import get_auth_headers'
    )
    sch = sch.replace(
        'from .market_data import get_auth_headers',
        'from .market_data import get_auth_headers, fetch_finnifty_direction'
    )
    print('WARN: added fetch_finnifty_direction via fallback')

# ── 4. Remove the stale check_rv_gate CALL block (lines ~276-281) ─────────────
old_rv_call = ('    # \u2500\u2500 Realized Volatility Gate (skip flat days) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n'
               '    rv = check_rv_gate(df)\n'
               '    if not rv[\'ok\']:\n'
               '        log.info(f\"[{now.strftime(\'%H:%M\')}] \u23f8\ufe0f RV gate: range {rv[\'range_pts\']:.1f}pts \"\n'
               '                 f\"({rv[\'range_pct\']:.2f}% \u003c {rv[\'required\']:.2f}% min) \u2014 flat session, skip\")\n'
               '        return\n')
if old_rv_call in sch:
    sch = sch.replace(old_rv_call, '')
    print('OK: check_rv_gate CALL removed from scalp_poll')
else:
    # Try simpler removal
    lines = sch.split('\n')
    out = []
    i = 0
    removed = 0
    while i < len(lines):
        ln = lines[i]
        if 'check_rv_gate' in ln or ('rv[' in ln and removed > 0):
            removed += 1
            i += 1
            continue
        if 'RV gate: range' in ln:
            removed += 1
            i += 1
            continue
        if 'flat session, skip' in ln:
            removed += 1
            i += 1
            continue
        if removed > 0 and ln.strip() == 'return' and i > 0 and 'RV' in lines[max(0,i-3):i][-1] if lines[max(0,i-3):i] else False:
            removed += 1
            i += 1
            continue
        out.append(ln)
        i += 1
    if removed > 0:
        sch = '\n'.join(out)
        print(f'OK: removed {removed} lines containing check_rv_gate call')
    else:
        print('WARN: could not find check_rv_gate call block')
        # Show context
        for j, ln in enumerate(sch.split('\n')):
            if 'check_rv_gate' in ln or 'rv[' in ln.lower():
                print(f'  line {j+1}: {ln}')

sp.write_text(sch, encoding='utf-8')

# ── 5. Final syntax check ──────────────────────────────────────────────────────
try:
    ast.parse(sch)
    print('SYNTAX OK: scheduler.py')
except SyntaxError as ex:
    print(f'SYNTAX ERR line {ex.lineno}: {ex.msg}')
    lines = sch.split('\n')
    for i in range(max(0, ex.lineno-3), min(len(lines), ex.lineno+3)):
        print(f'  {i+1}: {lines[i]}')
