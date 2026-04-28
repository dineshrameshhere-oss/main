import schedule
import time
import threading
from datetime import datetime

from .logger import log
from .config import TIME_PRE_MARKET, TIME_MARKET_OPEN, TIME_EOD_CHECK, MAX_TRADE_BUDGET, DEFAULT_SL_PCT, DEFAULT_TP_PCT
from .market_data import fetch_historical_ohlcv, compress_ohlcv_to_string, fetch_first_30min_candle, fetch_intraday_data
from .news_fetcher import fetch_nifty_news
from .indicators import compute_key_levels, compute_scalp_signals
from .llm_analyst import analyze_premarket, analyze_market_open
from .options_engine import select_strike, calculate_qty, calculate_dynamic_risk
from .trade_executor import place_order, get_balance
from .risk_manager import monitor_position

# Global state
class BotState:
    premarket_analysis = {}
    live_mode = False
    active_position = None
    daily_trades = 0

state = BotState()

def job_premarket():
    log.info("\n[09:00] 📊 Fetching pre-market data and news...")
    historical = fetch_historical_ohlcv()
    
    # We only care about 1h data for scalping context mostly, but let's grab daily levels
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
    
    direction = final_analysis.get("final_direction", "NO_TRADE")
    confidence = final_analysis.get("confidence", "LOW")
    
    log.info(f"🤖 LLM Final Decision: {direction} | Confidence: {confidence}")
    
    if "SCALP" in direction and confidence in ["HIGH", "MEDIUM"]:
        execute_scalp_trade(direction)
    else:
        log.info("🤖 LLM opted out or failed. Falling back to pure Technical Analysis...")
        execute_scalp_trade("SCALP_AUTO")

def execute_scalp_trade(direction):
    if state.daily_trades >= 3:
        log.warning("🚫 Max daily trades (3) reached. Stopping for the day.")
        return
        
    log.info(f"🚀 Initializing SCALP check for {direction}")
    
    # Check current balance first
    current_balance = get_balance(state.live_mode)
    if current_balance < 1500.0:
        log.warning(f"⚠️ Balance too low to trade safely (₹{current_balance:.2f}). Waiting for deposit.")
        return
    
    # Need current spot price
    df_3m = fetch_intraday_data(interval='5m', period='1d')
    if df_3m.empty: return
    spot = df_3m['Close'].iloc[-1]
    
    # Check TA signals
    signals = compute_scalp_signals(df_3m)
    log.info(f"📊 TA Signals: {signals}")
    
    if signals.get("confidence_score", 0) < 2:
        log.warning("⚠️ TA Signals too weak for entry. Skipping trade.")
        return
        
    if direction == "SCALP_AUTO":
        direction = signals["direction"]
        
    strike_info = select_strike(direction, spot, current_balance)
    premium = strike_info["simulated_premium"]
    
    qty, cost, lots = calculate_qty(current_balance, premium)
    
    if qty == 0: return
    
    # Dynamic Risk Profiling based on Premium Quality
    sl_pct, tp_pct = calculate_dynamic_risk(premium)
    
    sl_price = premium * (1 - sl_pct)
    tp_price = premium * (1 + tp_pct)
    
    log.info(f"🎯 Selected: NIFTY {strike_info['strike']} {strike_info['type']} | Premium: ₹{premium:.2f} | Qty: {qty} | Cost: ₹{cost:.2f}")
    log.info(f"🛡️ Dynamic Risk Config: SL {sl_pct*100}% | TP {tp_pct*100}%")
    
    order = place_order(strike_info['security_id'], direction, qty, premium, sl_price, tp_price, state.live_mode)
    if order:
        state.active_position = order
        state.daily_trades += 1
        # Start monitoring in a separate thread so scheduler isn't blocked
        threading.Thread(target=monitor_position, args=(order, state.live_mode)).start()

def job_eod():
    log.info("\n[15:15] 🛑 End of Day Triggered. Closing open positions...")
    if state.active_position and state.active_position.get('status') == 'OPEN':
        from .trade_executor import close_order
        close_order(state.active_position['order_id'], "EOD_EXIT", state.live_mode)
        state.active_position = None
    log.info("Good night! 🌙")

def start_scheduler(live_mode=False):
    state.live_mode = live_mode
    log.info(f"📅 Scheduler started in {'LIVE' if live_mode else 'PAPER'} mode.")
    
    schedule.every().day.at(TIME_PRE_MARKET).do(job_premarket)
    schedule.every().day.at(TIME_MARKET_OPEN).do(job_market_open)
    schedule.every().day.at(TIME_EOD_CHECK).do(job_eod)
    
    # Scalp polling every 3 minutes between 09:35 and 15:00
    def scalp_poll():
        now = datetime.now().strftime("%H:%M")
        if "09:35" <= now <= "15:00" and state.active_position is None:
            # Check if LLM gave us a bias to scalp today
            bias = state.premarket_analysis.get("strategy_suggestion", "")
            if "SCALP" in bias:
                execute_scalp_trade(bias)
            else:
                execute_scalp_trade("SCALP_AUTO")
                
    schedule.every(3).minutes.do(scalp_poll)
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("\n🛑 Bot manually stopped.")
