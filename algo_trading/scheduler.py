import schedule
import time
import threading
from datetime import datetime

from .logger import log
from .config import TIME_PRE_MARKET, TIME_MARKET_OPEN, TIME_EOD_CHECK
from .market_data import fetch_historical_ohlcv, compress_ohlcv_to_string, fetch_first_30min_candle, fetch_intraday_data
from .news_fetcher import fetch_nifty_news
from .indicators import compute_key_levels, compute_scalp_signals, compute_supertrend, compute_adx_series, compute_macd_hist_series, compute_orb_series, compute_multi_rating
from .llm_analyst import analyze_premarket, analyze_market_open
from .options_engine import select_strike, calculate_qty, calculate_dynamic_risk
from .trade_executor import place_order, get_balance
from .risk_manager import monitor_position
from .config import RATING_STRONG_BUY, RATING_STRONG_SELL, SUPERTREND_PERIOD, SUPERTREND_MULT, RSI_PERIOD

# Global state
class BotState:
    premarket_analysis = {}
    live_mode = False
    active_position = None
    daily_trades = 0
    last_rating_score = 0.0   # for crossover detection

state = BotState()


def _get_enriched_df():
    """Fetch live 5m data and attach all indicator columns needed by compute_multi_rating."""
    import pandas as pd
    import numpy as np

    df = fetch_intraday_data(interval='5minute', days_back=2)
    if df is None or df.empty:
        return None

    df = df.dropna()

    # ── IST timezone fix ──────────────────────────────────────────────────
    if df.index.tzinfo is None:
        df.index = (
            pd.to_datetime(df.index, utc=True)
            .tz_convert('Asia/Kolkata')
            .tz_localize(None)
        )

    # VWAP (daily reset)
    df['TradeDate'] = df.index.date
    tp  = (df['High'] + df['Low'] + df['Close']) / 3
    df['VWAP'] = (tp * df['Volume']).groupby(df['TradeDate']).cumsum() / \
                  df['Volume'].groupby(df['TradeDate']).cumsum()

    # SuperTrend
    df['ST_Dir'] = compute_supertrend(df, period=SUPERTREND_PERIOD, mult=SUPERTREND_MULT)

    # ADX
    df['ADX'] = compute_adx_series(df, period=14)

    # MACD histogram
    df['MACD_HIST'] = compute_macd_hist_series(df)

    # RSI (Wilder)
    rsi_delta = df['Close'].diff()
    rsi_gain  = rsi_delta.where(rsi_delta > 0, 0).ewm(com=RSI_PERIOD-1, adjust=False).mean()
    rsi_loss  = (-rsi_delta.where(rsi_delta < 0, 0)).ewm(com=RSI_PERIOD-1, adjust=False).mean()
    df['RSI'] = (100 - (100 / (1 + rsi_gain / rsi_loss.replace(0, 1e-9)))).fillna(50)

    # ORB
    df = compute_orb_series(df)

    return df


def job_premarket():
    log.info("\n[09:00] 📊 Fetching pre-market data and news...")
    historical = fetch_historical_ohlcv()

    df_1h = historical.get('1h')
    key_levels = compute_key_levels(df_1h)
    comp_1h = compress_ohlcv_to_string(df_1h, '1h')

    news = fetch_nifty_news()

    log.info("[09:02] 🤖 Asking Gemini for Pre-Market Analysis...")
    analysis = analyze_premarket(comp_1h, news, key_levels)
    state.premarket_analysis = analysis

    bias = analysis.get("strategy_suggestion", "WAIT")
    log.info(f"🤖 LLM Suggestion: {bias} | Confidence: {analysis.get('confidence')}")


def job_market_open():
    log.info("\n[09:30] 📈 Fetching first 30-min candle...")
    open_data = fetch_first_30min_candle()

    log.info("[09:31] 🤖 Asking Gemini for Market Open Confirmation...")
    final_analysis = analyze_market_open(state.premarket_analysis, open_data)

    direction  = final_analysis.get("final_direction", "NO_TRADE")
    confidence = final_analysis.get("confidence", "LOW")

    log.info(f"🤖 LLM Final Decision: {direction} | Confidence: {confidence}")


def execute_scalp_trade(rating_score: float, direction: str):
    """
    Called when multi-indicator rating crosses STRONG_BUY / STRONG_SELL threshold.
    direction: 'SCALP_LONG' (buy CE) or 'SCALP_SHORT' (buy PE)
    """
    if state.daily_trades >= 3:
        log.warning("🚫 Max daily trades (3) reached. No more entries today.")
        return

    current_balance = get_balance(state.live_mode)
    if current_balance < 1500.0:
        log.warning(f"⚠️ Balance too low (₹{current_balance:.2f}). Need ≥ ₹1500.")
        return

    df = _get_enriched_df()
    if df is None or df.empty:
        log.error("❌ Could not fetch live 5m data for trade execution.")
        return

    spot = float(df['Close'].iloc[-1])
    premium_target = current_balance / 25.0

    strike_info = select_strike(direction, spot, current_balance)
    premium = strike_info.get("simulated_premium", premium_target)

    qty, cost, lots = calculate_qty(current_balance, premium)
    if qty == 0:
        log.warning("⚠️ Qty = 0 (balance too low for 1 lot). Skipping.")
        return

    sl_pct, tp_pct = calculate_dynamic_risk(premium)
    sl_price = premium * (1 - sl_pct)
    tp_price = premium * (1 + tp_pct)

    opt_type = "CE" if direction == "SCALP_LONG" else "PE"
    log.info(f"🎯 NIFTY {strike_info['strike']} {opt_type} | "
             f"Premium: ₹{premium:.2f} | Qty: {qty} | Cost: ₹{cost:.2f} | "
             f"SL: ₹{sl_price:.2f} | TP: ₹{tp_price:.2f} | "
             f"Rating score: {rating_score:+.2f}")

    order = place_order(
        strike_info['security_id'], direction, qty,
        premium, sl_price, tp_price, state.live_mode
    )
    if order:
        state.active_position = order
        state.daily_trades += 1
        threading.Thread(
            target=monitor_position, args=(order, state.live_mode), daemon=True
        ).start()


def scalp_poll():
    """
    Runs every 3 minutes between 09:35 and 14:45 IST.
    Computes the multi-indicator rating and enters on STRONG_BUY/SELL crossover.
    """
    now_str = datetime.now().strftime("%H:%M")
    now_h   = datetime.now().hour
    now_m   = datetime.now().minute

    # ── Time guards (professional scalping hours only) ──────────────────
    if now_str < "09:35": return
    if now_str > "14:45": return
    if now_h == 12 or (now_h == 13 and now_m < 15): return  # noon lull

    # ── Already in a trade — skip entry logic ───────────────────────────
    if state.active_position is not None:
        return

    # ── Fetch live data and compute rating ──────────────────────────────
    df = _get_enriched_df()
    if df is None or df.empty:
        log.warning("⚠️ [Scalp Poll] No live data. Will retry next cycle.")
        return

    # Use last 50 bars as window
    window_df  = df.iloc[-50:].copy()
    rsi_window = window_df['RSI']
    adx_val    = float(df['ADX'].iloc[-1])
    macd_w     = window_df['MACD_HIST']

    rating = compute_multi_rating(window_df, rsi_window, adx_val, macd_w)
    score  = rating['score']
    bd     = rating['breakdown']

    log.info(
        f"[{now_str}] 📊 Rating: {rating['rating']} ({score:+.2f}) | "
        f"ADX:{bd.get('adx',0):.0f} RSI:{bd.get('rsi',0):.0f} "
        f"ORB:{bd.get('structure_orb',0):+.0f} "
        f"Vol:{'✅' if bd.get('volume_confirm') else '❌'}"
    )

    # ── Crossover detection ─────────────────────────────────────────────
    prev_score = state.last_rating_score
    state.last_rating_score = score

    if score >= RATING_STRONG_BUY and prev_score < RATING_STRONG_BUY:
        log.info(f"🟢 STRONG BUY signal! Score crossed +{RATING_STRONG_BUY} → Entering CE")
        execute_scalp_trade(score, "SCALP_LONG")

    elif score <= RATING_STRONG_SELL and prev_score > RATING_STRONG_SELL:
        log.info(f"🔴 STRONG SELL signal! Score crossed {RATING_STRONG_SELL} → Entering PE")
        execute_scalp_trade(score, "SCALP_SHORT")


def job_eod():
    log.info("\n[15:15] 🛑 End of Day — force-closing any open positions...")
    if state.active_position and state.active_position.get('status') == 'OPEN':
        from .trade_executor import close_order
        close_order(state.active_position['order_id'], "EOD_EXIT", state.live_mode)
        state.active_position = None
    state.daily_trades = 0    # reset for next day
    state.last_rating_score = 0.0
    log.info("✅ EOD complete. Good night! 🌙")


def start_scheduler(live_mode=False):
    state.live_mode = live_mode
    mode_str = "🔴 LIVE" if live_mode else "🟢 PAPER"
    log.info(f"\n{'='*50}")
    log.info(f"  {mode_str} MODE — Nifty 50 Scalping Bot")
    log.info(f"  Jobs: Pre-market 09:00 | Open 09:30 | EOD 15:15")
    log.info(f"  Scalp polling: every 3 min from 09:35 → 14:45")
    log.info(f"  Waiting for next scheduled event...")
    log.info(f"{'='*50}\n")

    schedule.every().day.at(TIME_PRE_MARKET).do(job_premarket)
    schedule.every().day.at(TIME_MARKET_OPEN).do(job_market_open)
    schedule.every().day.at(TIME_EOD_CHECK).do(job_eod)
    schedule.every(3).minutes.do(scalp_poll)

    # If started after 09:00, run a poll immediately so you see activity NOW
    now_str = datetime.now().strftime("%H:%M")
    if "09:35" <= now_str <= "14:45":
        log.info("⚡ Started during market hours — running first poll immediately...")
        scalp_poll()

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("\n🛑 Bot manually stopped with Ctrl+C")
