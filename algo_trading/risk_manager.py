import time
from .logger import log
from .trade_executor import close_order
from .config import TRAILING_SL_PCT

def monitor_position(order: dict, live: bool = False):
    """
    Monitors an active trade for SL, TP, or Trailing Stop.
    Optimized for fast 5-10 min scalps.
    """
    if not order: return

    order_id = order['order_id']
    entry = order['entry_price']
    sl = order['sl_price']
    tp = order['tp_price']
    qty = order['qty']
    
    peak_premium = entry
    
    log.info(f"👀 Monitoring Position {order_id} | Entry: ₹{entry:.2f}")

    while True:
        # In reality, fetch live LTP. Here we simulate random movements for paper trading if live=False
        # For Termux we could also fetch 1m yfinance data but it delays.
        # Let's mock the price movement for demonstration.
        
        import random
        # Simulate price moving by -2% to +3% every tick
        current_premium = peak_premium * random.uniform(0.98, 1.03) 
        
        pnl_pct = (current_premium - entry) / entry
        
        log.debug(f"📉 PnL: {pnl_pct*100:+.2f}% | LTP: ₹{current_premium:.2f} | Peak: ₹{peak_premium:.2f}")

        # 1. HARD STOP LOSS
        if current_premium <= sl:
            pnl_amount = (current_premium - entry) * qty
            log.warning(f"🛑 STOP LOSS HIT! Exit: ₹{current_premium:.2f} | Loss: ₹{pnl_amount:.2f}")
            close_order(order_id, "STOP_LOSS", pnl=pnl_amount, live=live)
            break
            
        # 2. HARD TAKE PROFIT
        if current_premium >= tp:
            pnl_amount = (current_premium - entry) * qty
            log.info(f"🎯 TAKE PROFIT HIT! Exit: ₹{current_premium:.2f} | Profit: ₹{pnl_amount:.2f}")
            close_order(order_id, "TAKE_PROFIT", pnl=pnl_amount, live=live)
            break
            
        # 3. TRAILING STOP (Activates if we are up at least 5%)
        if pnl_pct > 0.05:
            peak_premium = max(peak_premium, current_premium)
            trailing_sl = peak_premium * (1 - TRAILING_SL_PCT)
            if current_premium <= trailing_sl:
                pnl_amount = (current_premium - entry) * qty
                log.info(f"🏃 TRAILING STOP HIT! Exit: ₹{current_premium:.2f} | PnL: ₹{pnl_amount:.2f}")
                close_order(order_id, "TRAILING_STOP", pnl=pnl_amount, live=live)
                break
                
        time.sleep(5) # Poll every 5 seconds for scalping
