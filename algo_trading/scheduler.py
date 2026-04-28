import schedule
import time
import threading
from datetime import datetime, timezone, timedelta

from .logger import log
from .config import (TIME_PRE_MARKET, TIME_MARKET_OPEN, TIME_EOD_CHECK,
                     RATING_STRONG_BUY, RATING_STRONG_SELL,
                     SUPERTREND_PERIOD, SUPERTREND_MULT, RSI_PERIOD,
                     PCR_APPLY_AFTER_HOUR)
from .market_data import (fetch_historical_ohlcv, compress_ohlcv_to_string,
                           fetch_first_30min_candle, fetch_intraday_data)
from .news_fetcher import fetch_nifty_news
from .indicators import (compute_key_levels, compute_supertrend, compute_adx_series,
                          compute_macd_hist_series, compute_orb_series, compute_multi_rating,
                          check_rv_gate, _candle_quality, _momentum_signal, compute_greeks)
from .llm_analyst import analyze_premarket, analyze_market_open
from .options_engine import select_strike, calculate_qty, calculate_dynamic_risk, compute_pcr
from .trade_executor import place_order, get_balance, close_order
from .risk_manager import monitor_position

IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
#  BOT STATE  (single shared instance — no class-level mutable defaults)
# ─────────────────────────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.premarket_analysis: dict  = {}
        self.live_mode:          bool  = False
        self.active_position:    dict | None = None
        self.active_position_lock = threading.Lock()
        self.daily_trades:       int   = 0
        self.last_rating_score:  float = 0.0
        self.last_breakdown:     dict  = {}

state = BotState()


# ─────────────────────────────────────────────────────────────────────────────
#  LIVE DATA HELPER
# ─────────────────────────────────────────────────────────────────────────────
def _get_enriched_df():
    """Fetch 5m intraday data and compute all indicator columns."""
    import pandas as pd

    df = fetch_intraday_data(interval='5minute', days_back=2)
    if df is None or df.empty:
        return None

    df = df.dropna()
    if len(df) < 30:
        log.warning("Enriched DF too short (<30 bars). Skipping.")
        return None

    # IST conversion (no tzdata needed)
    if df.index.tzinfo is None:
        df.index = (
            pd.to_datetime(df.index, utc=True)
            .tz_convert(IST)
            .tz_localize(None)
        )

    # VWAP (daily reset)
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


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEDULED JOBS
# ─────────────────────────────────────────────────────────────────────────────
def job_premarket():
    log.info("\n[09:00] 📊 Pre-market scan starting...")
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
            close_order(pos['order_id'], 'EOD_EXIT', pnl=0.0, live=state.live_mode)
            state.active_position = None
    state.daily_trades = 0
    state.last_rating_score = 0.0
    log.info("✅ EOD done. Good night! 🌙")


# ─────────────────────────────────────────────────────────────────────────────
#  MONITOR CALLBACK  — called by monitor thread when position closes
# ─────────────────────────────────────────────────────────────────────────────
def _on_position_closed():
    """Called by the monitor thread when a position is fully closed."""
    with state.active_position_lock:
        state.active_position = None
    log.info("📭 Position cleared — bot ready for next signal.")


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
    df = df_enriched if (df_enriched is not None and not df_enriched.empty) else _get_enriched_df()
    if df is None or df.empty:
        log.error("❌ No live data for trade execution.")
        return

    # ── 1. Candle Quality Filter (false breakout) ──────────────────────────
    cq = _candle_quality(df, direction)
    if cq['is_rejection']:
        log.warning(f"⚠️ Candle rejected — {cq['reason']} | Skipping entry.")
        return
    log.info(f"  ✅ Candle quality OK (body {cq['body_ratio']:.0%})")

    # ── 2. Momentum Filter ─────────────────────────────────────────────────
    mom = _momentum_signal(df, direction)
    if not mom['ok']:
        log.warning(f"⚠️ Momentum weak — {mom['move_pct']:+.3f}% in 30 min "
                    f"(need ≥{mom['required_pct']:.3f}%) | Skipping.")
        return
    log.info(f"  ✅ Momentum OK ({mom['move_pct']:+.3f}% in 30 min)")

    # PCR is the 5th scoring component in compute_multi_rating (not a hard gate).
    # It already influenced the STRONG_BUY/SELL crossover threshold.
    if pcr:
        log.info(f"  📊 PCR={pcr.get('pcr',1.0):.2f} ({pcr.get('bias','N/A')}) | included in composite score")

    # ── 4. Strike selection ───────────────────────────────────────────────
    spot        = float(df['Close'].iloc[-1])
    strike_info = select_strike(direction, spot, current_balance)
    premium     = strike_info.get('simulated_premium', 0.0)

    if premium <= 0:
        log.warning("⚠️ No valid live premium — skipping trade.")
        return

    # ── 5. Greeks Gate (delta ≥ 0.20) ───────────────────────────────────────
    opt_type    = 'CE' if direction == 'SCALP_LONG' else 'PE'
    days_to_exp = strike_info.get('days_to_expiry', 7)
    greeks      = compute_greeks(spot, strike_info['strike'], days_to_exp, premium, opt_type)
    if greeks['delta'] < 0.20:
        log.warning(f"⚠️ Greeks gate: delta={greeks['delta']:.3f} < 0.20 (too deep OTM) | Skipping.")
        return
    log.info(f"  ✅ Greeks — δ={greeks['delta']:.3f} γ={greeks['gamma']:.5f} "
             f"θ={greeks['theta']:.1f}₹/day IV={greeks['iv_pct']:.0f}%")

    qty, cost, _ = calculate_qty(current_balance, premium)
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

    with state.active_position_lock:
        state.active_position = order
    state.daily_trades += 1

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

    # ── Fetch data & compute rating ─────────────────────────────────────
    df = _get_enriched_df()
    if df is None or df.empty:
        log.warning("⚠️ [Poll] No live data. Retry next cycle.")
        return

    # ── Realized Volatility Gate (skip flat days) ───────────────────────
    rv = check_rv_gate(df)
    if not rv['ok']:
        log.info(f"[{now.strftime('%H:%M')}] ⏸️ RV gate: range {rv['range_pts']:.1f}pts "
                 f"({rv['range_pct']:.2f}% < {rv['required']:.2f}% min) — flat session, skip")
        return

    # ── PCR (fetch once, reuse in execute) ─────────────────────────────
    pcr_data = compute_pcr()

    window_df  = df.iloc[-50:].copy()
    rsi_window = window_df['RSI']
    adx_val    = float(df['ADX'].iloc[-1]) if not df['ADX'].isna().all() else 20.0
    macd_w     = window_df['MACD_HIST']

    rating = compute_multi_rating(window_df, rsi_window, adx_val, macd_w, pcr=pcr_data)
    score  = rating['score']
    bd     = rating['breakdown']
    state.last_breakdown = bd   # stash for execute_scalp_trade OI log

    choppy_warn = " ⚠️CHOPPY" if bd.get('choppy') else ""
    oi_type     = bd.get('oi_coverage', '')
    pcr_txt     = f" PCR:{bd.get('pcr_val',1.0):.2f}" if 'pcr_val' in bd else ""
    log.info(
        f"[{now.strftime('%H:%M')}] 📊 {rating['rating']} ({score:+.2f}) | "
        f"ADX:{bd.get('adx',0):.0f}{choppy_warn} RSI:{bd.get('rsi',0):.0f} "
        f"ORB:{bd.get('structure_orb',0):+.0f} OI:{oi_type}{pcr_txt} "
        f"Vol:{'✅' if bd.get('volume_confirm') else '❌'} Trades:{state.daily_trades}/3"
    )

    prev_score            = state.last_rating_score
    state.last_rating_score = score

    if score >= RATING_STRONG_BUY and prev_score < RATING_STRONG_BUY:
        log.info(f"🟢 STRONG BUY crossed +{RATING_STRONG_BUY} → Buy CE")
        execute_scalp_trade(score, 'SCALP_LONG', df_enriched=df, pcr=pcr_data)
    elif score <= RATING_STRONG_SELL and prev_score > RATING_STRONG_SELL:
        log.info(f"🔴 STRONG SELL crossed {RATING_STRONG_SELL} → Buy PE")
        execute_scalp_trade(score, 'SCALP_SHORT', df_enriched=df, pcr=pcr_data)


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def start_scheduler(live_mode: bool = False):
    state.live_mode = live_mode
    mode_str = '🔴 LIVE' if live_mode else '🟢 PAPER'

    balance = get_balance(live_mode)
    log.info(f"\n{'='*52}")
    log.info(f"  {mode_str} MODE — Nifty 50 Scalping Bot")
    log.info(f"  Balance      : ₹{balance:.2f}")
    log.info(f"  Pre-market   : {TIME_PRE_MARKET}  |  Open: {TIME_MARKET_OPEN}  |  EOD: {TIME_EOD_CHECK}")
    log.info(f"  Scalp poll   : every 3 min | 09:35 → 14:45 (skip 12:00–13:15)")
    log.info(f"  Max trades   : 3/day  |  Max hold: 60 min")
    log.info(f"{'='*52}\n")

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
