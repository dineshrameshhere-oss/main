import schedule
import time
import threading
from datetime import datetime, timezone, timedelta

from .logger import log
from .config import (
    INTRADAY_POLL_INTERVAL_MIN, TIME_PRE_MARKET, TIME_MARKET_OPEN, TIME_EOD_CHECK
)
from .market_data import fetch_historical_ohlcv
from .dl_engine import compute_dl_rating
from .options_engine import select_strike, calculate_qty
from .trade_executor import place_order, get_balance, close_order
from .scheduler import state

def intraday_poll():
    """
    Main execution loop for the Intraday DL bot.
    Runs every 15 minutes.
    """
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    
    # ── Time Filters ──────────────────────────────────────────────────────────
    t_open = datetime.strptime(TIME_MARKET_OPEN, "%H:%M").time()
    t_eod  = datetime.strptime(TIME_EOD_CHECK, "%H:%M").time()
    
    if now.time() < t_open or now.time() > t_eod:
        return

    # Skip first 15 mins to avoid opening volatility
    if now.hour == 9 and now.minute < 30:
        return

    if state.active_position:
        log.debug("Intraday trade already active. Skipping entry poll.")
        return

    # ── Fetch Data & Compute Rating ───────────────────────────────────────────
    # We fetch 15-minute candles instead of 5-minute for the Intraday bot.
    df = fetch_historical_ohlcv(timeframes=['1mo', '1wk', '1d'])
    
    if df is None or df.empty or len(df) < 20:
        log.warning("⚠️ [Intraday Poll] No live data or insufficient candles. Retry next cycle.")
        return

    # Call the Deep Learning engine to get the rating
    rating = compute_dl_rating(df)
    score = rating.get("score", 0)
    direction = rating.get("direction", "NONE")
    
    bd = rating.get("breakdown", {})
    bull_p = bd.get("dl_bull_prob", 0)
    bear_p = bd.get("dl_bear_prob", 0)
    vol_p = bd.get("dl_vol_prob", 0)
    vega_p = bd.get("dl_vega_prob", 0)

    # Log the AI's output
    log.info(
        f"[{now.strftime('%H:%M')}] 🧠 DL {rating['rating']} ({score:+.1f}) | "
        f"Bull:{bull_p:.2f} Bear:{bear_p:.2f} Vol:{vol_p:.2f} Vega:{vega_p:.2f}"
    )

    if direction == "NONE":
        return

    # ── Execute Trade ─────────────────────────────────────────────────────────
    execute_intraday_trade(df, direction, score, rating)


def execute_intraday_trade(df, direction, score, rating):
    current_balance = get_balance()
    if current_balance < 1500.0:
        log.warning(f"⚠️ Balance ₹{current_balance:.2f} too low.")
        return

    spot = float(df['Close'].iloc[-1])
    strike_info = select_strike(direction, spot, current_balance)
    
    if not strike_info:
        log.warning(f"⚠️ No suitable {direction} strike found under budget.")
        return

    symbol = strike_info['symbol']
    premium = strike_info['premium']
    
    qty = calculate_qty(premium, current_balance)
    if qty <= 0:
        log.warning("⚠️ Insufficient funds for even 1 lot.")
        return

    log.info(f"🎯 INTRADAY {direction} TRIGGERED! | Score: {score:+.1f}")
    log.info(f"   Strike: {symbol} | Premium: ₹{premium} | Qty: {qty}")
    
    order_id = place_order(symbol, qty, "BUY", price=premium)
    if order_id:
        # Save trade to state
        state.active_position = {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "entry_price": premium,
            "direction": direction,
            "entry_time": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "highest_price": premium,
            "stop_loss": premium * 0.85,  # 15% Intraday SL default
            "type": "INTRADAY"
        }
        state.daily_trades += 1
        state.save_state()
        log.info(f"✅ INTRADAY TRADE ENTERED. Order ID: {order_id}")
    else:
        log.error("❌ Order placement failed.")


def start_intraday_scheduler(live_mode=True):
    """
    Initialises the 15-minute schedule for the Intraday bot.
    """
    mode = "🔴 LIVE TRADING" if live_mode else "🟢 PAPER TRADING"
    log.info("=" * 60)
    log.info(f"  🧠 INTRADAY DL OPTIONS BOT")
    log.info(f"  Mode      : {mode}")
    log.info(f"  Interval  : {INTRADAY_POLL_INTERVAL_MIN} minutes")
    log.info("=" * 60)

    # Note: For Termux, we use simple polling intervals rather than exact clock times
    schedule.every(INTRADAY_POLL_INTERVAL_MIN).minutes.do(intraday_poll)

    # Initial poll
    intraday_poll()

    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            log.info("Intraday Bot Stopped.")
            break
        except Exception as e:
            log.error(f"Scheduler Error: {e}")
            time.sleep(10)
