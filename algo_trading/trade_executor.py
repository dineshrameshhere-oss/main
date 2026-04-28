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
        "sl_limit_price": max(0.05, round(sl_price * 0.95, 2)), # slightly below trigger
        "tgt_limit_price": round(tp_price * 1.01, 2), # slightly above trigger
        "algo_id": ALGO_ID
    }
    
    try:
        url = f"{INDSTOCKS_BASE}/smart/order"
        res = requests.post(url, json=payload, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            resp_data = res.json().get('data', {}).get('order_data', [])
            if resp_data:
                # Capture the exact parent or child order ID for cancellation later
                actual_order_id = resp_data[0].get('child_order_details', {}).get('order_id', resp_data[0].get('order_id', order_id))
                order_details["order_id"] = actual_order_id
            
            log.info(f"✅ [LIVE TRADE] Smart Order {order_details['order_id']} placed successfully!")
            return order_details
        else:
            log.error(f"❌ [LIVE TRADE] Order Failed: {res.text}")
            return {}
    except Exception as e:
        log.error(f"❌ [LIVE TRADE] API Error: {e}")
        return {}

import json
import os

PAPER_BALANCE_FILE = "paper_balance.json"

def get_balance(live: bool = False) -> float:
    """
    Returns the current available trading balance.
    """
    if live:
        import requests
        from .market_data import get_auth_headers
        from .config import INDSTOCKS_BASE
        try:
            url = f"{INDSTOCKS_BASE}/user/margins"
            res = requests.get(url, headers=get_auth_headers(), timeout=5)
            if res.status_code == 200:
                data = res.json()
                return float(data.get('data', {}).get('available_margin', 0.0))
            else:
                log.error(f"❌ INDMoney Balance API Error: {res.status_code} - {res.text}")
        except Exception as e:
            log.error(f"❌ [LIVE TRADE] API Error fetching balance: {e}")
        return 0.0
        
    if not os.path.exists(PAPER_BALANCE_FILE):
        # Initialize paper wallet with 2000
        with open(PAPER_BALANCE_FILE, 'w') as f:
            json.dump({"balance": 2000.0}, f)
        return 2000.0
        
    with open(PAPER_BALANCE_FILE, 'r') as f:
        data = json.load(f)
        return float(data.get("balance", 2000.0))

def update_balance_pnl(pnl: float):
    """
    Updates the paper trading balance with a realized PnL.
    """
    current = get_balance(live=False)
    new_balance = current + pnl
    with open(PAPER_BALANCE_FILE, 'w') as f:
        json.dump({"balance": new_balance}, f)
    log.info(f"💼 Paper Balance Updated: ₹{current:.2f} -> ₹{new_balance:.2f} (PnL: ₹{pnl:.2f})")

def close_order(order_id: str, exit_reason: str, pnl: float = 0.0, live: bool = False):
    """
    Closes an open position and records PnL.
    """
    if not live:
        log.info(f"🏁 [PAPER TRADE] Position {order_id} Closed. Reason: {exit_reason} | PnL: ₹{pnl:.2f}")
        update_balance_pnl(pnl)
        return True
        
    log.info(f"🏁 [LIVE TRADE] Closing Position {order_id}. Reason: {exit_reason}")
    import requests
    from .market_data import get_auth_headers
    from .config import INDSTOCKS_BASE
    
    payload = {
        "order_id": order_id,
        "segment": "DERIVATIVE"
    }
    try:
        url = f"{INDSTOCKS_BASE}/smart/order/cancel"
        res = requests.post(url, json=payload, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            log.info(f"✅ [LIVE TRADE] Position {order_id} successfully cancelled/closed!")
            return True
        else:
            log.error(f"❌ [LIVE TRADE] Cancel Failed: {res.text}")
    except Exception as e:
        log.error(f"❌ [LIVE TRADE] API Error closing order: {e}")
    return False
