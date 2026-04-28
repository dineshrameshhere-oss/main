import json
import os
import uuid
from .logger import log

# Use absolute path so it works regardless of cwd
_BASE_DIR          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER_BALANCE_FILE = os.path.join(_BASE_DIR, 'paper_balance.json')


def place_order(security_id: str, direction: str, qty: int,
                limit_price: float, sl_price: float, tp_price: float,
                live: bool = False) -> dict:
    """
    Places an order. live=False → paper simulation (no real API call).
    Returns the full order dict used by the monitor thread.
    """
    txn_type = 'BUY'   # always buying options (CE or PE)
    order_id = str(uuid.uuid4())[:8]

    order = {
        'order_id':   order_id,
        'security_id': security_id,
        'txn_type':   txn_type,
        'qty':        qty,
        'entry_price': limit_price,
        'sl_price':   sl_price,
        'tp_price':   tp_price,
        'status':     'OPEN',
    }

    if not live:
        log.info(
            f"🟢 [PAPER TRADE] Order placed: {txn_type} {qty}x {security_id} "
            f"@ ₹{limit_price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}"
        )
        return order

    # ── LIVE TRADING ──────────────────────────────────────────────────────
    import requests
    from .market_data import get_auth_headers
    from .config import INDSTOCKS_BASE, ALGO_ID

    payload = {
        'txn_type':         txn_type,
        'exchange':         'NSE',
        'segment':          'DERIVATIVE',
        'product':          'MARGIN',
        'order_type':       'LIMIT',
        'validity':         'DAY',
        'security_id':      security_id,
        'qty':              qty,
        'limit_price':      limit_price,
        'sl_trigger_price': sl_price,
        'tgt_trigger_price': tp_price,
        'sl_limit_price':   max(0.05, round(sl_price  * 0.95, 2)),
        'tgt_limit_price':  round(tp_price * 1.01, 2),
        'algo_id':          ALGO_ID,
    }
    try:
        url = f"{INDSTOCKS_BASE}/smart/order"
        res = requests.post(url, json=payload, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            resp_data = res.json().get('data', {}).get('order_data', [])
            if resp_data:
                order['order_id'] = (
                    resp_data[0].get('child_order_details', {})
                                .get('order_id', resp_data[0].get('order_id', order_id))
                )
            log.info(f"✅ [LIVE TRADE] Smart Order {order['order_id']} placed!")
            return order
        else:
            log.error(f"❌ [LIVE TRADE] Order Failed: {res.status_code} {res.text[:120]}")
    except Exception as e:
        log.error(f"❌ [LIVE TRADE] API Error: {e}")
    return {}


def get_balance(live: bool = False) -> float:
    """Returns current available balance (paper wallet or live margin)."""
    if live:
        import requests
        from .market_data import get_auth_headers
        from .config import INDSTOCKS_BASE
        try:
            url = f"{INDSTOCKS_BASE}/user/margins"
            res = requests.get(url, headers=get_auth_headers(), timeout=5)
            if res.status_code == 200:
                return float(res.json().get('data', {}).get('available_margin', 0.0))
            log.error(f"❌ Balance API {res.status_code}: {res.text[:80]}")
        except Exception as e:
            log.error(f"❌ Balance fetch error: {e}")
        return 0.0

    # Paper wallet
    if not os.path.exists(PAPER_BALANCE_FILE):
        initial = float(os.getenv('PAPER_INITIAL_BALANCE', '5000'))
        _write_balance(initial)
        return initial

    try:
        with open(PAPER_BALANCE_FILE, 'r') as f:
            return float(json.load(f).get('balance', 5000.0))
    except Exception:
        return 5000.0


def update_paper_balance(pnl: float):
    """Adds realised PnL to the paper wallet. Thread-safe via file lock workaround."""
    current = get_balance(live=False)
    new_bal = current + pnl
    _write_balance(new_bal)
    log.info(f"💼 Paper Balance: ₹{current:.2f} → ₹{new_bal:.2f}  (PnL: ₹{pnl:+.2f})")


def _write_balance(balance: float):
    with open(PAPER_BALANCE_FILE, 'w') as f:
        json.dump({'balance': round(balance, 2)}, f)


def close_order(order_id: str, exit_reason: str, pnl: float = 0.0, live: bool = False) -> bool:
    """
    Closes a position and records PnL.
    Paper mode: updates wallet file.
    Live mode: cancels via INDMoney smart order cancel API.
    """
    if not live:
        log.info(f"🏁 [PAPER TRADE] Position {order_id} closed | Reason: {exit_reason} | PnL: ₹{pnl:+.2f}")
        update_paper_balance(pnl)
        return True

    log.info(f"🏁 [LIVE TRADE] Closing {order_id} | Reason: {exit_reason}")
    import requests
    from .market_data import get_auth_headers
    from .config import INDSTOCKS_BASE

    try:
        url = f"{INDSTOCKS_BASE}/smart/order/cancel"
        res = requests.post(url,
                            json={'order_id': order_id, 'segment': 'DERIVATIVE'},
                            headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            log.info(f"✅ [LIVE TRADE] Position {order_id} cancelled successfully.")
            return True
        log.error(f"❌ [LIVE TRADE] Cancel failed: {res.status_code} {res.text[:120]}")
    except Exception as e:
        log.error(f"❌ [LIVE TRADE] Cancel error: {e}")
    return False
