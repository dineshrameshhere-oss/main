import schedule
import time
import threading
import os
import json
from datetime import datetime, timezone, timedelta

from .logger import log
from .config import (
    RATING_AFTERNOON_RELAXED, AFTERNOON_HOUR, AFTERNOON_MIN,
    TIME_PRE_MARKET, TIME_MARKET_OPEN, TIME_EOD_CHECK,
    RATING_STRONG_BUY, RATING_STRONG_SELL,
    SUPERTREND_PERIOD, SUPERTREND_MULT, RSI_PERIOD)
from .market_data import (fetch_historical_ohlcv, compress_ohlcv_to_string,
                           fetch_first_30min_candle, fetch_intraday_data,
                           fetch_finnifty_direction, fetch_iv_rank, resample_ohlcv,
                           is_market_holiday)
from .news_fetcher import fetch_nifty_news
from .indicators import (
    compute_key_levels, compute_supertrend, compute_adx_series,
    compute_macd_hist_series, compute_orb_series, compute_multi_rating,
    compute_greeks, check_rv_gate, compute_mtf_trend_score
)
from .llm_analyst import analyze_premarket, analyze_market_open
from .options_engine import select_strike, calculate_qty, calculate_dynamic_risk, compute_pcr
from .trade_executor import place_order, get_balance, close_order
from .risk_manager import monitor_position

IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
#  BOT STATE  (single shared instance — no class-level mutable defaults)
# ─────────────────────────────────────────────────────────────────────────────
_BASE_DIR  = os.path.dirname(__file__)
STATE_FILE = os.path.join(_BASE_DIR, 'bot_state.json')

class BotState:
    def __init__(self):
        self.premarket_analysis: dict  = {}
        self.live_mode:          bool  = False
        self.use_ai:             bool  = False
        self.active_position:    dict | None = None
        self.active_position_lock = threading.Lock()
        self.daily_trades:       int   = 0
        self.consecutive_losses: int   = 0
        self.last_rating_score:  float = 0.0
        self.last_breakdown:     dict  = {}
        self.market_status:      dict  = {"is_open": True, "reason": "Not checked", "date": None}
        self.last_loss_time:     datetime | None = None
        self.last_loss_dir:      str | None = None
        self.load_state()

    def save_state(self):
        """Persists critical state to disk to survive Termux/system restarts."""
        try:
            data = {
                "daily_trades":       self.daily_trades,
                "consecutive_losses": self.consecutive_losses,
                "active_position":    self.active_position,
                "last_loss_time":     self.last_loss_time.isoformat() if self.last_loss_time else None,
                "last_loss_dir":      self.last_loss_dir,
                "date":               datetime.now(IST).strftime("%Y-%m-%d")
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            log.error(f"Error saving state: {e}")

    def load_state(self):
        """Loads state from disk if it belongs to the current trading day."""
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            
            # Only load if it's from today
            today = datetime.now(IST).strftime("%Y-%m-%d")
            if data.get("date") == today:
                self.daily_trades       = data.get("daily_trades", 0)
                self.consecutive_losses = data.get("consecutive_losses", 0)
                self.active_position    = data.get("active_position")
                self.last_loss_dir      = data.get("last_loss_dir")
                ltime = data.get("last_loss_time")
                if ltime:
                    self.last_loss_time = datetime.fromisoformat(ltime)
                log.info(f"🔄 State recovered: {self.daily_trades} trades, {self.consecutive_losses} losses.")
                if self.active_position:
                    log.warning(f"⚠️ Recovered ACTIVE POSITION: {self.active_position['security_id']}")
        except Exception as e:
            log.error(f"Error loading state: {e}")

state = BotState()

def _check_market_open_dynamic():
    """
    Checks if market is open today using a fast, direct API check.
    """
    today = datetime.now(IST).date()
    if state.market_status["date"] == today:
        return not state.market_status["is_open"], state.market_status["reason"]
    
    is_holiday, reason = is_market_holiday()
    state.market_status = {
        "is_open": not is_holiday,
        "reason": reason,
        "date": today
    }
    return is_holiday, reason


# ─────────────────────────────────────────────────────────────────────────────
#  LIVE DATA HELPER
# ─────────────────────────────────────────────────────────────────────────────
def _get_enriched_df():
    """Fetch 1m intraday data and resample to 3m, 5m, 15m for MTF analysis."""
    import pandas as pd
    from datetime import datetime

    # Fetch 1m data (base for all resampling)
    df_1m = fetch_intraday_data(interval='1minute', days_back=2)
    if df_1m is None or df_1m.empty:
        return None, None, None, None

    # ── STALE DATA / HOLIDAY CHECK ──────────────────────────────────────────
    # Check official holiday calendar first (via Dynamic Google Search)
    is_holiday, reason = _check_market_open_dynamic()
    if is_holiday:
        log.warning(f"🚫 Market is CLOSED today: {reason}. Skipping poll.")
        return None, None, None, None

    # Fallback to data timestamp check
    now_ist = datetime.now(IST)
    latest_ts = df_1m.index[-1]
    
    # If timestamp doesn't have tz, assume it's UTC from API and convert to IST
    if latest_ts.tzinfo is None:
        latest_ts = pd.to_datetime(latest_ts, utc=True).tz_convert(IST)
    else:
        latest_ts = latest_ts.astimezone(IST)

    # If the latest data is more than 15 minutes old during market hours, it's stale.
    # If the latest data is from yesterday, it's a holiday/weekend.
    if latest_ts.date() < now_ist.date():
        log.warning(f"🚫 Market appears CLOSED/HOLIDAY. Latest data is from {latest_ts.date()}.")
        return None, None, None, None

    # Check for excessive API lag (more than 15 mins) during active hours
    if (now_ist - latest_ts).total_seconds() > 15 * 60:
        log.warning(f"⚠️ API data is lagging by {(now_ist - latest_ts).total_seconds()/60:.0f} mins. Skipping for safety.")
        return None, None, None, None
        
    # Check for frozen price (zero volatility over last 5 bars)
    if len(df_1m) > 5:
        last_5 = df_1m.iloc[-5:]
        if last_5['Close'].max() == last_5['Close'].min():
            log.warning("⚠️ Market data is FROZEN (no price movement). Skipping poll.")
            return None, None, None, None

    # IST conversion
    if df_1m.index.tzinfo is None:
        df_1m.index = (
            pd.to_datetime(df_1m.index, utc=True)
            .tz_convert(IST)
            .tz_localize(None)
        )

    # Resample to needed timeframes
    df_3m  = resample_ohlcv(df_1m, '3min')
    df_5m  = resample_ohlcv(df_1m, '5min')
    df_15m = resample_ohlcv(df_1m, '15min')

    def add_indicators(df):
        if df.empty: return df
        df = df.copy()
        df['TradeDate'] = df.index.date
        tp = (df['High'] + df['Low'] + df['Close']) / 3
        df['VWAP'] = (
            (tp * df['Volume']).groupby(df['TradeDate']).cumsum()
            / df['Volume'].groupby(df['TradeDate']).cumsum()
        )
        df['ST_Dir']    = compute_supertrend(df, period=SUPERTREND_PERIOD, mult=SUPERTREND_MULT)
        df['ADX']       = compute_adx_series(df, period=14)
        df['MACD_HIST'] = compute_macd_hist_series(df)

        rsi_delta = df['Close'].diff()
        rsi_gain  = rsi_delta.where(rsi_delta > 0, 0).ewm(com=RSI_PERIOD-1, adjust=False).mean()
        rsi_loss  = (-rsi_delta.where(rsi_delta < 0, 0)).ewm(com=RSI_PERIOD-1, adjust=False).mean()
        df['RSI'] = (100 - (100 / (1 + rsi_gain / rsi_loss.replace(0, 1e-9)))).fillna(50)
        
        df = compute_orb_series(df)
        return df

    df_3m  = add_indicators(df_3m)
    df_5m  = add_indicators(df_5m)
    df_15m = add_indicators(df_15m)

    return df_3m, df_5m, df_15m, df_1m


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEDULED JOBS
# ─────────────────────────────────────────────────────────────────────────────
def job_premarket():
    log.info("\n[09:00] 📊 Pre-market scan starting...")
    
    # ── Market Availability Check (Dynamic Search) ──────────────────────────
    is_holiday, reason = _check_market_open_dynamic()
    if is_holiday:
        log.warning(f"🚫 Market is CLOSED today: {reason}. Bot will remain idle.")
        return

    if not state.use_ai:
        log.info("[09:00] 🤖 AI analysis disabled. Skipping Pre-market scan.")
        return

    try:
        historical = fetch_historical_ohlcv()
        df_1h      = historical.get('1h')
        key_levels = compute_key_levels(df_1h)
        comp_1h    = compress_ohlcv_to_string(df_1h, '1h')
        news       = fetch_nifty_news()

        log.info("[09:02] 🤖 Asking Gemini for Pre-Market Analysis...")
        analysis = analyze_premarket(comp_1h, news, key_levels)
        state.premarket_analysis = analysis

        bias = analysis.get('strategy_suggestion', 'WAIT')
        log.info(f"🤖 LLM: {bias} | Confidence: {analysis.get('confidence')} | {analysis.get('reasoning','')[:80]}")
    except Exception as e:
        log.error(f"job_premarket error: {e}")


def job_market_open():
    log.info("\n[09:30] 📈 Market open scan...")
    if not state.use_ai:
        log.info("[09:30] 🤖 AI analysis disabled. Skipping Market-open scan.")
        return

    try:
        open_data      = fetch_first_30min_candle()
        final_analysis = analyze_market_open(state.premarket_analysis, open_data)
        direction      = final_analysis.get('final_direction', 'NO_TRADE')
        confidence     = final_analysis.get('confidence', 'LOW')
        log.info(f"🤖 LLM Open: {direction} | Confidence: {confidence}")
    except Exception as e:
        log.error(f"job_market_open error: {e}")


def job_eod():
    log.info("\n[15:15] 🛑 End of Day — force-closing open positions...")
    with state.active_position_lock:
        pos = state.active_position
        if pos and pos.get('status') == 'OPEN':
            close_order(pos['order_id'], 'EOD_EXIT', pnl=0.0, live=state.live_mode,
                        security_id=pos.get('security_id'), qty=pos.get('qty', 0))
            state.active_position = None
    state.daily_trades = 0
    state.last_rating_score = 0.0
    log.info("✅ EOD done. Good night! 🌙")


# ─────────────────────────────────────────────────────────────────────────────
#  MONITOR CALLBACK  — called by monitor thread when position closes
# ─────────────────────────────────────────────────────────────────────────────
def _on_position_closed(pnl: float = 0.0):
    """Called by the monitor thread when a position is fully closed."""
    with state.active_position_lock:
        # Check if it was a loss
        if pnl < 0:
            state.consecutive_losses += 1
            # Record loss for directional cooldown
            pos = state.active_position
            if pos:
                state.last_loss_time = datetime.now(IST)
                state.last_loss_dir  = "CALL" if "CE" in pos.get('security_id', '') else "PUT"
        else:
            state.consecutive_losses = 0
            # Reset loss tracking on profit
            if hasattr(state, 'last_loss_time'): del state.last_loss_time
            if hasattr(state, 'last_loss_dir'):  del state.last_loss_dir

        state.active_position = None
        state.save_state()  # Persist immediately on trade close
            
    log.info(f"📭 Position cleared — bot ready for next signal. (Consecutive Losses: {state.consecutive_losses})")


# ─────────────────────────────────────────────────────────────────────────────
#  TRADE EXECUTION
# ─────────────────────────────────────────────────────────────────────────────
def execute_scalp_trade(rating_score: float, direction: str,
                        df_enriched=None, pcr: dict | None = None):
    """Enters a trade after passing all quality filters."""
    if state.daily_trades >= 3:
        log.warning("🚫 Max 3 trades/day reached.")
        return

    current_balance = get_balance(state.live_mode)
    if current_balance < 1500.0:
        log.warning(f"⚠️ Balance ₹{current_balance:.2f} too low (need ≥ ₹1500).")
        return

    # Use already-fetched df from poll — no double API call
    if df_enriched is not None and not df_enriched.empty:
        df = df_enriched
    else:
        _, df, _, _ = _get_enriched_df()

    if df is None or df.empty:
        log.error("❌ No live data for trade execution.")
        return

    # Candle quality + momentum are now scored inside compute_multi_rating
    # (candle_sig weight=0.7, mom_sig weight=0.6) — no hard gates here.
    # The crossover threshold already ensured both confirmed the direction.
    if pcr:
        log.info(f"  📊 PCR={pcr.get('pcr',1.0):.2f} ({pcr.get('bias','N/A')}) | included in composite score")


    # ── 4. Strike selection ───────────────────────────────────────────────
    spot        = float(df['Close'].iloc[-1])
    strike_info = select_strike(direction, spot, current_balance)
    premium     = strike_info.get('simulated_premium', 0.0)

    if premium <= 0:
        log.warning("⚠️ No valid live premium — skipping trade.")
        return

    # ── Greeks: score contribution (no hard gate) ──────────────────────────────
    opt_type    = 'CE' if direction == 'SCALP_LONG' else 'PE'
    days_to_exp = strike_info.get('days_to_expiry', 7)
    greeks      = compute_greeks(spot, strike_info['strike'], days_to_exp, premium, opt_type)
    # Delta score: deep OTM options (delta<0.2) lower score but don't block
    greek_bonus = 0.0
    if   greeks['delta'] >= 0.45: greek_bonus = +0.08  # near ATM — good sensitivity
    elif greeks['delta'] >= 0.30: greek_bonus = +0.03
    elif greeks['delta'] <  0.15: greek_bonus = -0.10  # deep OTM — penalise
    
    # Check if Greek adjustment pulls score below execution threshold
    # For SELL signals (negative score), we check if score + bonus is > -threshold
    is_long = direction == 'SCALP_LONG'
    effective_score = rating_score + (greek_bonus if is_long else -greek_bonus)
    threshold = RATING_STRONG_BUY * 0.90
    
    if (is_long and effective_score < threshold) or (not is_long and effective_score > -threshold):
        log.warning(f"⚠️ Greek penalty pulls score below threshold — δ={greeks['delta']:.3f} | Skipping.")
        return
    log.info(f"  ✅ Greeks — δ={greeks['delta']:.3f} γ={greeks['gamma']:.5f} "
             f"θ={greeks['theta']:.1f}₹/day IV={greeks['iv_pct']:.0f}% greek_bonus={greek_bonus:+.2f}")

    is_strong = abs(rating_score) >= 0.85
    qty, cost, _ = calculate_qty(current_balance, premium, is_strong_conviction=is_strong)
    if qty == 0:
        log.warning(f"⚠️ Insufficient balance for 1 lot @ ₹{premium:.2f}.")
        return

    sl_pct, tp_pct = calculate_dynamic_risk(premium)
    sl_price = round(premium * (1 - sl_pct), 2)
    tp_price = round(premium * (1 + tp_pct), 2)

    # OI coverage for log (from rating breakdown if available)
    bd       = getattr(state, 'last_breakdown', {})
    oi_type  = bd.get('oi_coverage', 'N/A')
    log.info(
        f"🎯 NIFTY {strike_info['strike']} {opt_type} | "
        f"Premium ₹{premium:.2f} | Qty {qty} | Cost ₹{cost:.0f} | "
        f"SL ₹{sl_price:.2f} | TP ₹{tp_price:.2f} | "
        f"Score {rating_score:+.2f} | OI:{oi_type}"
    )

    order = place_order(
        strike_info['security_id'], direction, qty,
        premium, sl_price, tp_price, state.live_mode
    )
    if not order:
        return

    # Monitor thread takes over
    with state.active_position_lock:
        state.active_position = order
        state.daily_trades += 1
        state.save_state()  # Persist immediately on trade start

    # Run the risk monitor in a separate thread
    threading.Thread(
        target=monitor_position,
        args=(order, state.live_mode, _on_position_closed),
        daemon=True
    ).start()



# ─────────────────────────────────────────────────────────────────────────────
#  SCALP POLL  (every 3 minutes during market hours)
# ─────────────────────────────────────────────────────────────────────────────
def scalp_poll():
    now     = datetime.now(IST)
    now_hm  = now.hour * 60 + now.minute   # integer minutes since midnight

    # ── Time guards ────────────────────────────────────────────────────────
    OPEN  = 9 * 60 + 35    # 09:35
    CLOSE = 14 * 60 + 45   # 14:45
    NOON_S = 12 * 60        # 12:00
    NOON_E = 13 * 60 + 15   # 13:15

    if now_hm < OPEN or now_hm > CLOSE:
        return
    if NOON_S <= now_hm <= NOON_E:
        return

    # ── Already in a trade? ─────────────────────────────────────────────
    with state.active_position_lock:
        in_trade = state.active_position is not None
    if in_trade:
        return

    # ── Circuit Breaker: Consecutive Losses ─────────────────────────────
    if state.consecutive_losses >= 2:
        log.warning(f"🚫 Circuit breaker active: {state.consecutive_losses} consecutive losses. Trading stopped for today.")
        return

    # ── Fetch data & compute rating ─────────────────────────────────────
    df_3m, df_5m, df_15m, df_1m = _get_enriched_df()
    if df_5m is None or df_5m.empty:
        log.warning("⚠️ [Poll] No live data. Retry next cycle.")
        return

    # ── Realized Volatility Gate (skip flat days) ───────────────────────
    rv_gate = check_rv_gate(df_5m)
    if not rv_gate['ok']:
        log.warning(f"⚠️ [Poll] RV Gate: Market too flat ({rv_gate['range_pct']}% < {rv_gate['required']}%). Skipping.")
        return

    # ── PCR + FinNifty + IVR (fetch once per poll cycle) ──────────────────
    pcr_data    = compute_pcr()
    fnf_dir     = fetch_finnifty_direction()   # 0.0 on error (safe neutral)
    ivr_data    = fetch_iv_rank()              # {'ivr':50,'iv':0,'signal':0.0} on error
    ivr_sig     = ivr_data.get('signal', 0.0)
    
    # ── MTF Trend Confirmation ──────────────────────────────────────────
    mtf_score = compute_mtf_trend_score(df_3m, df_5m, df_15m)

    window_df  = df_5m.iloc[-50:].copy()
    rsi_window = window_df['RSI']
    adx_val    = float(df_5m['ADX'].iloc[-1]) if not df_5m['ADX'].isna().all() else 20.0
    macd_w     = window_df['MACD_HIST']

    # ── History for Anti-Whipsaw (Attach previous scores) ──────────────
    if not hasattr(state, 'score_history'):
        state.score_history = []
    
    rating = compute_multi_rating(window_df, rsi_window, adx_val, macd_w,
                                   pcr=pcr_data, fnf_direction=fnf_dir, ivr_signal=ivr_sig,
                                   score_history=state.score_history, mtf_score=mtf_score)
    score  = rating['score']
    bd     = rating['breakdown']

    # ── DIRECTIONAL TIMEOUT (Anti-Revenge) ──────────────────────────────
    # If a recent loss occurred, block re-entry in same direction for 30m
    now_ist = datetime.now(IST)
    is_blocked = False
    if state.last_loss_time and state.last_loss_dir:
        time_since_loss = (now_ist - state.last_loss_time).total_seconds() / 60
        if time_since_loss < 30:
            intended_dir = "CALL" if score >= 0.3 else ("PUT" if score <= -0.3 else "NONE")
            if intended_dir == state.last_loss_dir and intended_dir != "NONE":
                log.warning(f"🚫 Directional Cooldown: Blocked {intended_dir} entry ({time_since_loss:.0f}m since last loss).")
                is_blocked = True
    
    if is_blocked:
        score = 0.0

    state.last_breakdown = bd   # stash for execute_scalp_trade OI log

    # Update history for NEXT poll
    state.score_history.append(score)
    if len(state.score_history) > 10:
        state.score_history.pop(0)

    choppy_warn = " ⚠️CHOPPY" if bd.get('choppy') else ""
    oi_type     = bd.get('oi_coverage', '')
    pcr_txt     = f" PCR:{bd.get('pcr_val',1.0):.2f}" if 'pcr_val' in bd else ""
    log.info(
        f"[{now.strftime('%H:%M')}] Sigma {rating['rating']} ({score:+.2f}) | "
        f"MTF:{mtf_score:+.2f} ADX:{bd.get('adx',0):.0f}{choppy_warn} RSI:{bd.get('rsi',0):.0f} "
        f"ORB:{bd.get('structure_orb',0):+.0f} OI:{oi_type}{pcr_txt} "
        f"IVR:{ivr_data.get('ivr',50):.0f}({ivr_sig:+.2f}) "
        f"Vol:{'OK' if bd.get('volume_confirm') else 'NO'} Trades:{state.daily_trades}/3"
    )

    prev_score            = state.last_rating_score
    state.last_rating_score = score

    # Afternoon safety net: if 0 trades by 12:30, relax threshold slightly
    _strong_thresh = RATING_STRONG_BUY
    
    # ── Morning Volatility Buffer (09:35 - 10:00) ────────────────────────
    # Require higher conviction during the chaotic opening period.
    is_morning = (9 * 60 + 35) <= now_hm <= (10 * 60 + 0)
    if is_morning:
        _strong_thresh = 0.85  # Strict threshold for morning entries
        log.debug(f"Morning Volatility Buffer: threshold raised to {_strong_thresh}")

    if (state.daily_trades == 0
            and now.hour > AFTERNOON_HOUR
            or (now.hour == AFTERNOON_HOUR and now.minute >= AFTERNOON_MIN)):
        _strong_thresh = RATING_AFTERNOON_RELAXED
        log.debug(f"Afternoon relaxed threshold: {_strong_thresh} (0 trades today)")

    # ── LLM Bias Filter ──────────────────────────────────────────────────
    # If Gemini pre-market analysis is enabled and has a bias, we follow it.
    if state.use_ai:
        premarket_bias = state.premarket_analysis.get('strategy_suggestion', 'WAIT')
        
        if score >= _strong_thresh and prev_score < _strong_thresh:
            if premarket_bias == 'BEARISH':
                log.warning(f"⚠️ STRONG BUY blocked by LLM BEARISH bias ({premarket_bias})")
                return
            log.info(f"🟢 STRONG BUY crossed +{RATING_STRONG_BUY} → Buy CE")
            execute_scalp_trade(score, 'SCALP_LONG', df_enriched=df_5m, pcr=pcr_data)
        elif score <= -_strong_thresh and prev_score > -_strong_thresh:
            if premarket_bias == 'BULLISH':
                log.warning(f"⚠️ STRONG SELL blocked by LLM BULLISH bias ({premarket_bias})")
                return
            log.info(f"🔴 STRONG SELL crossed {RATING_STRONG_SELL} → Buy PE")
            execute_scalp_trade(score, 'SCALP_SHORT', df_enriched=df_5m, pcr=pcr_data)
    else:
        # Standard execution without AI bias
        if score >= _strong_thresh and prev_score < _strong_thresh:
            log.info(f"🟢 STRONG BUY crossed +{RATING_STRONG_BUY} → Buy CE")
            execute_scalp_trade(score, 'SCALP_LONG', df_enriched=df_5m, pcr=pcr_data)
        elif score <= -_strong_thresh and prev_score > -_strong_thresh:
            log.info(f"🔴 STRONG SELL crossed {RATING_STRONG_SELL} → Buy PE")
            execute_scalp_trade(score, 'SCALP_SHORT', df_enriched=df_5m, pcr=pcr_data)


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def start_scheduler(live_mode: bool = False, use_ai: bool = False):
    state.live_mode = live_mode
    state.use_ai = use_ai
    mode_str = '🔴 LIVE' if live_mode else '🟢 PAPER'
    ai_str = 'Enabled' if use_ai else 'Disabled'

    balance = get_balance(live_mode)
    log.info(f"\n{'='*52}")
    log.info(f"  {mode_str} MODE — Nifty 50 Scalping Bot")
    log.info(f"  Balance      : ₹{balance:.2f}")
    log.info(f"  AI Features  : {ai_str}")
    log.info(f"  Pre-market   : {TIME_PRE_MARKET}  |  Open: {TIME_MARKET_OPEN}  |  EOD: {TIME_EOD_CHECK}")
    log.info(f"  Scalp poll   : every 3 min | 09:35 → 14:45 (skip 12:00–13:15)")
    log.info(f"  Max trades   : 3/day  |  Max hold: 60 min")
    log.info(f"{'='*52}\n")

    # ── Resume Monitoring if needed ─────────────────────────────────────
    if state.active_position:
        log.warning(f"🚀 Resuming monitor for recovered position: {state.active_position['order_id']}")
        from .risk_manager import monitor_position
        threading.Thread(
            target=monitor_position,
            args=(state.active_position['order_id'], 
                  state.active_position['security_id'],
                  state.active_position['entry_price'],
                  state.active_position['sl_price'],
                  state.active_position['tp_price'],
                  live_mode,
                  _on_position_closed),
            daemon=True
        ).start()

    schedule.every().day.at(TIME_PRE_MARKET).do(job_premarket)
    schedule.every().day.at(TIME_MARKET_OPEN).do(job_market_open)
    schedule.every().day.at(TIME_EOD_CHECK).do(job_eod)
    schedule.every(3).minutes.do(scalp_poll)

    # Immediate poll if started during market hours
    now     = datetime.now(IST)
    now_hm  = now.hour * 60 + now.minute
    if (9 * 60 + 35) <= now_hm <= (14 * 60 + 45):
        log.info("⚡ Market hours active — running first poll now...")
        scalp_poll()

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("\n🛑 Bot stopped with Ctrl+C")
