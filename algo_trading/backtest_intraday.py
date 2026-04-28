import pandas as pd
import requests
import time
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import math
import sys

from algo_trading.logger import log
from algo_trading.config import (
    INDSTOCKS_BASE, NIFTY_SCRIP_CODE, LOT_SIZE,
    INTRADAY_TP_PCT, INTRADAY_SL_PCT, INTRADAY_MAX_HOLD_MIN
)
from algo_trading.dl_engine import compute_dl_rating

def _fetch_15m_paginated(days=60):
    """
    Fetches 60 days of 15m candle data for backtesting Intraday DL models.
    """
    _ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    load_dotenv(_ENV)
    token = os.getenv("INDSTOCKS_TOKEN", "")
    headers = {"Authorization": token}
    
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
    
    all_candles = []
    chunk_end = end_ms
    
    print(f"Fetching {days} days of 15m Nifty data...")
    
    while chunk_end > start_ms:
        chunk_start = max(start_ms, chunk_end - (7 * 24 * 60 * 60 * 1000))
        url = (f"{INDSTOCKS_BASE}/market/historical/5minute"
               f"?scrip-codes={NIFTY_SCRIP_CODE}"
               f"&start_time={chunk_start}&end_time={chunk_end}")
               
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                candles = data.get('data', {}).get(NIFTY_SCRIP_CODE, {}).get('candles', [])
                if not candles:
                    print(f"  API returned empty candles list. Breaking.")
                    break
                all_candles.extend(candles)
                
                d_s = datetime.fromtimestamp(chunk_start/1000).strftime('%Y-%m-%d')
                d_e = datetime.fromtimestamp(chunk_end/1000).strftime('%Y-%m-%d')
                print(f"  Fetched 5m chunk: {d_s} -> {d_e} ({len(candles)} bars)")
                
                # Walk BACKWARD: use oldest bar timestamp (candles[0]) as new ceiling
                oldest_ts = candles[0].get('ts', 0) * 1000
                chunk_end = oldest_ts - 1000
                time.sleep(0.3)
            else:
                print(f"API Error {res.status_code}: {res.text}")
                break
        except Exception as e:
            print(f"Fetch error: {e}")
            break

    if not all_candles:
        return pd.DataFrame()

    # Deduplicate and sort
    df = pd.DataFrame(all_candles)
    df.drop_duplicates(subset=['ts'], inplace=True)
    df.sort_values('ts', ascending=True, inplace=True)
    df.rename(columns={'ts': 'Timestamp', 'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume'}, inplace=True)
    df['Date'] = pd.to_datetime(df['Timestamp'], unit='s') + pd.Timedelta(hours=5, minutes=30) # IST
    df.set_index('Date', inplace=True)
    
    # Resample 5m to 15m
    df_15m = df.resample('15min').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    print(f"Total 15m bars after resampling: {len(df_15m)}")
    return df_15m

def run_intraday_backtest(initial_capital=5000.0, days=60):
    """
    Backtests the Deep Learning Intraday strategy.
    Unlike scalping, intraday evaluates wider targets (40% TP) over hours.
    """
    df = _fetch_15m_paginated(days)
    if df.empty:
        print("No data fetched. Aborting.")
        return

    # Backtest constants
    capital = initial_capital
    position = None
    
    trades = []
    wins = 0
    losses = 0
    max_hold_bars = INTRADAY_MAX_HOLD_MIN // 15
    
    for i in range(20, len(df)):
        window = df.iloc[:i]
        current_bar = df.iloc[i]
        bar_time = current_bar.name.time()
        
        # ── 1. Manage Active Position ─────────────────────────────────────────
        if position:
            position['bars_held'] += 1
            entry_spot  = position['entry_spot']

            # Scale-invariant P&L: use % move of Nifty directly
            # Works correctly whether API returns 1107 or 24000
            nifty_move_pct = (current_bar['Close'] - entry_spot) / max(abs(entry_spot), 1e-9) * 100
            if position['direction'] == 'PUT':
                nifty_move_pct = -nifty_move_pct

            # Scale-invariant P&L using trade_cost (30% of capital)
            # At ATM with delta=0.55: a 1% Nifty move = 73% option P&L
            # leverage_factor = (REAL_NIFTY × DELTA) / ATM_PREMIUM = 24000×0.55/180 = 73.3
            NIFTY_SL_PCT      = -0.50   # -0.5% Nifty → -36% option P&L
            NIFTY_TP_PCT      =  1.00   # +1.0% Nifty → +73% option P&L
            LEVERAGE          = (24000.0 * 0.55) / 180.0   # ~73.3
            trade_cost        = position['trade_cost']
            # P&L in Rs proportional to amount risked
            option_pnl_rs     = (nifty_move_pct / 100) * LEVERAGE * trade_cost

            # SL hit
            if nifty_move_pct <= NIFTY_SL_PCT:
                capital += option_pnl_rs
                position['exit_reason'] = "STOP_LOSS"
                position['pnl_nifty_pct'] = round(nifty_move_pct, 3)
                trades.append(position)
                losses += 1
                position = None
                continue

            # TP hit
            if nifty_move_pct >= NIFTY_TP_PCT:
                capital += option_pnl_rs
                position['exit_reason'] = "TAKE_PROFIT"
                position['pnl_nifty_pct'] = round(nifty_move_pct, 3)
                trades.append(position)
                wins += 1
                position = None
                continue

            # Time limit (4h = 16 bars of 15m)
            if position['bars_held'] >= max_hold_bars:
                capital += option_pnl_rs
                position['exit_reason'] = "TIME_LIMIT"
                position['pnl_nifty_pct'] = round(nifty_move_pct, 3)
                trades.append(position)
                if nifty_move_pct > 0: wins += 1
                else: losses += 1
                position = None
                continue

            # EOD exit
            if bar_time.hour == 15 and bar_time.minute >= 15:
                capital += option_pnl_rs
                position['exit_reason'] = "EOD"
                position['pnl_nifty_pct'] = round(nifty_move_pct, 3)
                trades.append(position)
                if nifty_move_pct > 0: wins += 1
                else: losses += 1
                position = None

                continue
                
            continue

        # ── 2. Scan for New Entries ───────────────────────────────────────────
        
        # Skip 9:15-9:30 and 12:00-13:15
        if bar_time.hour == 9 and bar_time.minute < 30: continue
        if bar_time.hour == 12 or (bar_time.hour == 13 and bar_time.minute <= 15): continue
        if bar_time.hour >= 14 and bar_time.minute >= 45: continue
        
        # Get ML Rating — accept STRONG and regular BUY/SELL
        # STRONG = direction + vol surge + vega expansion (all 3 confirmed)
        # BUY/SELL = direction + at least 1 of vol/vega confirmed
        rating = compute_dl_rating(window)
        bd     = rating.get("breakdown", {})
        
        if rating['rating'] not in ["STRONG_BUY", "STRONG_SELL", "BUY", "SELL"]:
            continue
        if rating['direction'] == "NONE":
            continue

        # Risk 30% of capital per intraday trade (manageable for small account)
        # ₹5000 × 0.30 = ₹1,500 per trade
        RISK_PCT      = 0.30
        trade_cost    = capital * RISK_PCT
        mock_premium  = 180.0
        if trade_cost < mock_premium:
            continue  # not even ₹180 left

        position = {
            'entry_time':  current_bar.name,
            'direction':   rating['direction'],
            'entry_spot':  current_bar['Close'],
            'entry_price': mock_premium,
            'trade_cost':  trade_cost,
            'bars_held':   0,
            'score':       rating['score'],
            'rating':      rating['rating'],
            'dl_stats':    bd,
        }

    # ── Print Report ──────────────────────────────────────────────────────────
    print("\n" + "="*56)
    print(f"  DL INTRADAY BACKTEST RESULTS ({days} days) | Capital: Rs {initial_capital}")
    print("="*56)
    
    total_trades = len(trades)
    if total_trades == 0:
        print("  No trades taken.")
        return
        
    win_rate = (wins / total_trades) * 100
    
    print(f"  Total Trades       : {total_trades}")
    print(f"  Wins               : {wins}  |  Losses: {losses}")
    print(f"  Win Rate           : {win_rate:.1f}%")
    
    sl_exits = sum(1 for t in trades if t['exit_reason'] == 'STOP_LOSS')
    tp_exits = sum(1 for t in trades if t['exit_reason'] == 'TAKE_PROFIT')
    time_exits = sum(1 for t in trades if t['exit_reason'] == 'TIME_LIMIT')
    eod_exits = sum(1 for t in trades if t['exit_reason'] == 'EOD')
    
    print(f"  Take Profit hits   : {tp_exits}")
    print(f"  Stop Loss hits     : {sl_exits}")
    print(f"  Time exits (4h)    : {time_exits}")
    print(f"  EOD exits          : {eod_exits}")
    print("-" * 56)
    
    net_pnl = capital - initial_capital
    ret_pct = (net_pnl / initial_capital) * 100
    print(f"  Started with       : Rs {initial_capital:.2f}")
    print(f"  Ended with         : Rs {capital:.2f}")
    print(f"  Net Trading PnL    : Rs {net_pnl:+.2f}")
    print(f"  Return             : {ret_pct:+.1f}%")
    print("="*56)

if __name__ == "__main__":
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    try:
        cap = float(sys.argv[1]) if len(sys.argv) > 1 else 5000.0
    except:
        cap = 5000.0
    run_intraday_backtest(initial_capital=cap)
