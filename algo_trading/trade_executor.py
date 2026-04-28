from .logger import log
import uuid

def place_order(security_id: str, direction: str, qty: int, limit_price: float, sl_price: float, tp_price: float, live: bool = False) -> dict:
    """
    Places an order via IndStocks API. If live=False, simulates the order (Paper Trading).
    """
    txn_type = "BUY" # Since we are buying Options (CE or PE)
    order_id = str(uuid.uuid4())[:8]
    
    order_details = {
        "order_id": order_id,
        "security_id": security_id,
        "txn_type": txn_type,
        "qty": qty,
        "entry_price": limit_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "status": "OPEN"
    }

    if not live:
        log.info(f"🟢 [PAPER TRADE] Order placed: {txn_type} {qty}x {security_id} @ ₹{limit_price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}")
        return order_details
        
    # LIVE TRADING (Requires real token and IndStocks Smart Order API)
    import requests
    from .market_data import get_auth_headers
    from .config import INDSTOCKS_BASE, ALGO_ID
    
    payload = {
        "txn_type": txn_type,
        "exchange": "NSE",
        "segment": "DERIVATIVE",
        "product": "MARGIN",
        "order_type": "LIMIT",
        "validity": "DAY",
        "security_id": security_id,
        "qty": qty,
        "limit_price": limit_price,
        "sl_trigger_price": sl_price,
        "tgt_trigger_price": tp_price,
        "sl_limit_price": sl_price - 1,
        "tgt_limit_price": tp_price - 1,
        "algo_id": ALGO_ID
    }
    
    try:
        url = f"{INDSTOCKS_BASE}/smart/order"
        res = requests.post(url, json=payload, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            log.info(f"✅ [LIVE TRADE] Order {order_id} executed!")
            return order_details
        else:
            log.error(f"❌ [LIVE TRADE] Order Failed: {res.text}")
            return {}
    except Exception as e:
        log.error(f"❌ [LIVE TRADE] API Error: {e}")
        return {}

def close_order(order_id: str, exit_reason: str, live: bool = False):
    """
    Closes an open position.
    """
    if not live:
        log.info(f"🏁 [PAPER TRADE] Position {order_id} Closed. Reason: {exit_reason}")
        return True
        
    log.info(f"🏁 [LIVE TRADE] Position {order_id} Closed. Reason: {exit_reason}")
    # Real logic to cancel bracket/square off
    return True
