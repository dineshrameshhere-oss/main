import pandas as pd
import time as _time
from algo_trading.config import (
    EMA_FAST, EMA_SLOW, RSI_PERIOD,
    VOLUME_MULT_SCALP, SUPERTREND_PERIOD, SUPERTREND_MULT,
    DEFAULT_SL_PCT, DEFAULT_TP_PCT, TRAILING_STEPS, RATING_STRONG_BUY, RATING_STRONG_SELL
)
from algo_trading.market_data import get_auth_headers
from algo_trading.config import INDSTOCKS_BASE, NIFTY_SCRIP_CODE
from algo_trading.indicators import (
    compute_supertrend, compute_adx_series,
    compute_macd_hist_series, compute_orb_series, compute_multi_rating
)
import numpy as np
import requests


def _fetch_5m_paginated(total_days: int = 60, chunk_days: int = 7) -> pd.DataFrame:
    """
    INDMoney caps 5-minute history to ~5 trading days per call.
    This function fetches data in overlapping weekly chunks and concatenates them.
    """
    all_frames = []
    now_ms     = int(_time.time() * 1000)
    day_ms     = 24 * 60 * 60 * 1000

    # Walk backward in chunk_days windows
    end_ms = now_ms
    fetched_days = 0
    while fetched_days < total_days:
        start_ms = end_ms - (chunk_days * day_ms)
        url = (
            f"{INDSTOCKS_BASE}/market/historical/5minute"
            f"?scrip-codes={NIFTY_SCRIP_CODE}"
            f"&start_time={start_ms}&end_time={end_ms}"
        )
        try:
            res = requests.get(url, headers=get_auth_headers(), timeout=10)
            if res.status_code == 200:
                data    = res.json()
                candles = data.get('data', {}).get(NIFTY_SCRIP_CODE, {}).get('candles', [])
                if candles:
                    chunk = pd.DataFrame(candles)
                    chunk.rename(columns={
                        'ts':'Timestamp','o':'Open','h':'High',
                        'l':'Low','c':'Close','v':'Volume'
                    }, inplace=True)
                    # API returns UTC epoch seconds — convert to IST (UTC+5:30)
                    from datetime import timezone, timedelta
                    IST = timezone(timedelta(hours=5, minutes=30))
                    chunk['Date'] = (
                        pd.to_datetime(chunk['Timestamp'], unit='s', utc=True)
                        .dt.tz_convert(IST)
                        .dt.tz_localize(None)          # strip tz-info for pandas compat
                    )
                    chunk.set_index('Date', inplace=True)
                    all_frames.append(chunk)
                    fetched_days += chunk.index.normalize().nunique()
                    print(f"  Fetched chunk: {chunk.index[0].date()} -> {chunk.index[-1].date()} ({len(chunk)} bars)")
                else:
                    # No more data available further back
                    break
            else:
                print(f"  API error {res.status_code} fetching chunk — stopping pagination.")
                break
        except Exception as e:
            print(f"  Fetch error: {e} — stopping pagination.")
            break

        end_ms = start_ms  # move window back
        _time.sleep(0.3)   # polite delay between calls

    if not all_frames:
        return pd.DataFrame()

    df = pd.concat(all_frames).sort_index()
    df = df[~df.index.duplicated(keep='first')]  # remove overlapping rows
    return df


def _stepped_sl_floor(pnl_pct: float) -> float:
    """
    Returns the SL floor (as % of entry premium) for the given profit level.
    Mirrors risk_manager._get_stepped_sl_floor exactly so backtest == live.
    Returns -DEFAULT_SL_PCT if no step has been triggered yet.
    """
    for trigger, floor in reversed(TRAILING_STEPS):
        if pnl_pct >= trigger:
            return floor
    return -DEFAULT_SL_PCT  # original hard SL

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def run_backtest(initial_capital: float = 5000.0):
    print(f"Fetching 60 days of 5m Nifty data | Starting capital: Rs {initial_capital:.0f}")
    df = _fetch_5m_paginated(total_days=60, chunk_days=7)

    if df is None or df.empty:
        print("Failed to fetch data. Check INDSTOCKS_TOKEN in .env.")
        return

    df = df.dropna()
    print(f"Total bars loaded: {len(df)} across {df.index.normalize().nunique()} trading days.")
    
    # Calculate indicators
    df['EMA_F'] = df['Close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['EMA_S'] = df['Close'].ewm(span=EMA_SLOW, adjust=False).mean()
    df['RSI'] = calculate_rsi(df['Close'], RSI_PERIOD)
    
    df['TradeDate'] = df.index.date
    df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['TPV'] = df['TP'] * df['Volume']
    
    df['Cum_Vol'] = df.groupby('TradeDate')['Volume'].cumsum()
    df['Cum_TPV'] = df.groupby('TradeDate')['TPV'].cumsum()
    df['VWAP'] = df['Cum_TPV'] / df['Cum_Vol']
    
    df['Vol_Avg'] = df['Volume'].rolling(20).mean()

    # SuperTrend
    df['ST_Dir'] = compute_supertrend(df, period=SUPERTREND_PERIOD, mult=SUPERTREND_MULT)

    # ATR (Wilder smoothing) — SL/TP sizing
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift(1)).abs(),
        (df['Low']  - df['Close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(alpha=1/14, adjust=False).mean()

    # ADX — trend strength (chop filter)
    df['ADX'] = compute_adx_series(df, period=14)

    # MACD histogram — momentum confirmation
    df['MACD_HIST'] = compute_macd_hist_series(df)

    # RSI series (Wilder)
    rsi_delta   = df['Close'].diff()
    rsi_gain    = rsi_delta.where(rsi_delta > 0, 0).ewm(com=RSI_PERIOD-1, adjust=False).mean()
    rsi_loss    = (-rsi_delta.where(rsi_delta < 0, 0)).ewm(com=RSI_PERIOD-1, adjust=False).mean()
    df['RSI']   = (100 - (100 / (1 + rsi_gain / rsi_loss.replace(0, 1e-9)))).fillna(50)

    # Opening Range Breakout (ORB 9:15–9:44 IST per day)
    df = compute_orb_series(df)
    
    # Simulate trades
    in_trade = False
    trade_dir = 0
    entry_price = 0
    prev_long_score = 0
    prev_short_score = 0

    # Per-trade state
    current_premium_target = 0
    current_qty            = 1
    peak_pnl_pct           = 0.0
    sl_floor_pct           = -DEFAULT_SL_PCT
    current_sl_pts         = 0.0
    current_tp_pts         = 0.0
    entry_bar_idx          = 0
    MAX_HOLD_BARS          = 12    # 12 bars × 5min = 60-min max hold
    prev_rating_score      = 0.0   # for crossover detection

    # Capital simulation
    capital           = initial_capital
    trades            = []
    daily_trades      = 0
    current_date      = None

    # Filter skip counters (mutable list so nested loops can increment)

    for i in range(50, len(df)):
        row = df.iloc[i]
        date = row.name.date()
        
        if date != current_date:
            current_date = date
            daily_trades = 0
            # EOD day-boundary exit — use PREVIOUS bar's close (last bar of prior day)
            if in_trade:
                prev_close   = df['Close'].iloc[i - 1]
                est_delta    = min(0.8, max(0.2, current_premium_target / 300.0))
                exit_pnl_pts = (prev_close - entry_price) * trade_dir
                exit_pnl_inr = exit_pnl_pts * est_delta * current_qty
                capital += exit_pnl_inr
                trades.append({
                    "date": date, "type": "LONG" if trade_dir == 1 else "SHORT",
                    "entry": entry_price, "exit": prev_close,
                    "pnl_inr": exit_pnl_inr, "capital": capital,
                    "premium": current_premium_target, "exit_reason": "EOD_DAY_BOUNDARY",
                    "peak_pnl_pct": round(peak_pnl_pct * 100, 1),
                    "sl_floor_pct": round(sl_floor_pct * 100, 1),
                })
                in_trade = False
                
        # 1. Manage Open Trade
        if in_trade:
            est_delta = min(0.8, max(0.2, current_premium_target / 300.0))
            bars_held = i - entry_bar_idx

            # ── Compute bar's best PnL% in option-premium terms ───────────────
            # Premium move ≈ Nifty move × delta / entry_premium
            if trade_dir == 1:
                best_nifty_move = row['High'] - entry_price
                close_nifty_move = row['Close'] - entry_price
            else:
                best_nifty_move  = entry_price - row['Low']
                close_nifty_move = entry_price - row['Close']

            bar_best_pnl_pct  = (best_nifty_move  * est_delta) / max(current_premium_target, 1)
            bar_close_pnl_pct = (close_nifty_move * est_delta) / max(current_premium_target, 1)

            # Track peak (for trailing SL ratchet)
            if bar_best_pnl_pct > peak_pnl_pct:
                peak_pnl_pct = bar_best_pnl_pct
                sl_floor_pct = _stepped_sl_floor(peak_pnl_pct)

            # ── TP / SL checks in premium-% space ────────────────────────────
            # TP: did this bar's best move hit DEFAULT_TP_PCT (8%)?
            tp_hit = bar_best_pnl_pct >= DEFAULT_TP_PCT

            # SL: worst move this bar in option terms
            if trade_dir == 1:
                worst_nifty_move = entry_price - row['Low']
            else:
                worst_nifty_move = row['High'] - entry_price
            bar_worst_pnl_pct = -(worst_nifty_move * est_delta) / max(current_premium_target, 1)

            # Use trailing floor if active, else hard SL
            effective_sl_pct = sl_floor_pct if sl_floor_pct > -DEFAULT_SL_PCT else -DEFAULT_SL_PCT
            sl_hit = bar_worst_pnl_pct <= effective_sl_pct

            exit_hit     = False
            exit_pnl_pct = 0.0

            if tp_hit:
                exit_hit = True; exit_pnl_pct = DEFAULT_TP_PCT; reason = "TAKE_PROFIT"
            elif sl_hit:
                exit_hit = True; exit_pnl_pct = effective_sl_pct
                reason = "TRAILING_SL" if sl_floor_pct >= 0 else "HARD_SL"

            # ── 60-min max hold: exit at close-price pnl ─────────────────────
            if not exit_hit and bars_held >= MAX_HOLD_BARS:
                exit_hit = True; exit_pnl_pct = bar_close_pnl_pct; reason = "TIME_EXIT"

            if exit_hit:
                exit_pnl_inr = exit_pnl_pct * current_premium_target * current_qty
                capital += exit_pnl_inr
                trades.append({
                    "date": date, "type": "LONG" if trade_dir == 1 else "SHORT",
                    "entry": entry_price, "exit": 0,
                    "pnl_inr": exit_pnl_inr, "capital": capital,
                    "premium": current_premium_target, "exit_reason": reason,
                    "peak_pnl_pct": round(peak_pnl_pct * 100, 1),
                    "sl_floor_pct": round(sl_floor_pct * 100, 1),
                })
                in_trade = False
                continue
                
            # EOD Force close at 15:15
            if in_trade and row.name.time().hour == 15 and row.name.time().minute >= 15:
                est_delta       = min(0.8, max(0.2, current_premium_target / 300.0))
                close_nifty_pct = ((row['Close'] - entry_price) * trade_dir * est_delta) / max(current_premium_target, 1)
                exit_pnl_inr    = close_nifty_pct * current_premium_target * current_qty
                capital += exit_pnl_inr
                trades.append({
                    "date": date, "type": "LONG" if trade_dir == 1 else "SHORT",
                    "entry": entry_price, "exit": row['Close'],
                    "pnl_inr": exit_pnl_inr, "capital": capital,
                    "premium": current_premium_target, "exit_reason": "EOD",
                    "peak_pnl_pct": round(peak_pnl_pct * 100, 1),
                    "sl_floor_pct": round(sl_floor_pct * 100, 1),
                })
                in_trade = False
                continue
            
        # 2. Look for Entry
        if daily_trades >= 3: continue
        time_h = row.name.time().hour
        time_m = row.name.time().minute

        # ── PROFESSIONAL TIME FILTERS (IST) ───────────────────────────────────
        # Rule 1: No trades in first 15 min (9:15-9:29) — too volatile
        if time_h == 9 and time_m < 30: continue
        # Rule 2: No trades during noon lull (12:00-13:15) — low volume/choppy
        if time_h == 12 or (time_h == 13 and time_m < 15): continue
        # Rule 3: No option buying after 14:45 — theta decay kills premium
        if time_h >= 15 or (time_h == 14 and time_m > 45): continue
        # Rule 4: ORB only valid after 9:44 — must wait for range to form
        # (handled inside compute_multi_rating — orb_sig=0 if before 9:45)

        # ── Option premium: realistic fixed price (not capital-derived) ──────
        # Nifty OTM CALL/PUT intraday: typical ₹150-250 depending on IV.
        # API Nifty is scaled ~1107 (real ~24000), so delta/premium math stays
        # in real-world terms. We use a fixed ₹200 ATM premium.
        ATM_PREMIUM   = 200.0           # Rs per unit (realistic OTM intraday)
        MAX_BUDGET    = min(capital * 0.30, 2000.0)   # risk max 30% or ₹2000
        qty           = max(1, int(MAX_BUDGET / ATM_PREMIUM))
        premium_target = ATM_PREMIUM
        if premium_target * qty > capital:
            continue   # not enough capital even for 1 unit

        # ── MULTI-INDICATOR RATING ────────────────────────────────────────────
        window_df   = df.iloc[max(0, i-50):i+1].copy()
        rsi_window  = window_df['RSI']
        adx_val     = float(row['ADX']) if not pd.isna(row['ADX']) else 20.0
        macd_window = window_df['MACD_HIST']

        rating = compute_multi_rating(window_df, rsi_window, adx_val, macd_window)
        score  = rating['score']

        # ── ENTRY: STRONG_BUY / STRONG_SELL crossover only ─────────────────
        new_long  = score >= RATING_STRONG_BUY  and prev_rating_score < RATING_STRONG_BUY
        new_short = score <= RATING_STRONG_SELL and prev_rating_score > RATING_STRONG_SELL

        if not new_long and not new_short:
            prev_rating_score = score   # neutral bar — consume score normally
            continue

        direction = 'SCALP_LONG' if new_long else 'SCALP_SHORT'

        # ── All filters passed — enter trade ────────────────────────────────
        prev_rating_score = score
        in_trade   = True
        trade_dir  = 1 if new_long else -1
        entry_price            = float(row['Close'])
        daily_trades           += 1
        current_premium_target = ATM_PREMIUM
        current_qty            = qty
        entry_bar_idx          = i
        peak_pnl_pct           = 0.0
        sl_floor_pct           = -DEFAULT_SL_PCT
        atr = float(row['ATR']) if not pd.isna(row['ATR']) else 3.0
        current_sl_pts = max(atr * 2.5, 5.0)
        current_tp_pts = max(atr * 2.5, 6.0)


    # Analysis
    if not trades:
        print("No trades executed.")
        return

    wins = [t for t in trades if t['pnl_inr'] > 0]
    losses = [t for t in trades if t['pnl_inr'] <= 0]
    
    trailing_exits = [t for t in trades if t.get('exit_reason') == 'TRAILING_SL']
    hard_sl_exits  = [t for t in trades if t.get('exit_reason') == 'HARD_SL']
    tp_exits       = [t for t in trades if t.get('exit_reason') == 'TAKE_PROFIT']
    time_exits     = [t for t in trades if t.get('exit_reason') == 'TIME_EXIT']
    eod_exits      = [t for t in trades if t.get('exit_reason', '').startswith('EOD')]
    avg_peak       = sum(t.get('peak_pnl_pct', 0) for t in trades) / len(trades)
    avg_floor      = sum(t.get('sl_floor_pct', 0) for t in wins)  / len(wins) if wins else 0

    avg_premium     = sum(t.get('premium', 0) for t in trades) / len(trades)
    avg_est_delta   = min(0.8, max(0.2, avg_premium / 300.0))

    print("\n" + "="*56)
    print(f"  BACKTEST RESULTS (60 days) | Capital: Rs {initial_capital:.0f}")
    print("  Signal : STRONG_BUY / STRONG_SELL only")
    print("  Filters: Skip 9:15-9:30 | 12:00-13:15 | after 14:45")
    print("  Guards : ADX<20 blocks STRONG | Vol confirm required")
    print("="*56)
    print(f"  Option avg premium : Rs {avg_premium:.0f}/unit | delta ~{avg_est_delta:.2f}")
    print(f"  Total Trades       : {len(trades)}")
    print(f"  Wins               : {len(wins)}  |  Losses: {len(losses)}")
    print(f"  Win Rate           : {len(wins)/len(trades)*100:.1f}%")
    print(f"  Avg Peak PnL       : +{avg_peak:.1f}%  (how far trades ran before exit)")
    print(f"  Avg Locked Floor   : +{avg_floor:.1f}%  (avg profit locked on wins)")
    print(f"  Hard SL exits      : {len(hard_sl_exits)}")
    print(f"  Trailing SL exits  : {len(trailing_exits)}")
    print(f"  Take Profit hits   : {len(tp_exits)}")
    print(f"  Time exits (60m)   : {len(time_exits)}")
    print(f"  EOD exits          : {len(eod_exits)}")
    print("-"*56)
    print("-"*56)
    print(f"  Started with       : Rs {initial_capital:.2f}")
    print(f"  Ended with         : Rs {capital:.2f}")
    print(f"  Net Trading PnL    : Rs {capital - initial_capital:+.2f}")
    print(f"  Return             : {((capital - initial_capital) / initial_capital)*100:+.1f}%")
    print("="*56)

if __name__ == "__main__":
    import sys
    cap = float(sys.argv[1]) if len(sys.argv) > 1 else 2000.0
    run_backtest(initial_capital=cap)
