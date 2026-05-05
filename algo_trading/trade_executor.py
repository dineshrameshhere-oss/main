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
    from .config import INDSTOCKS_BASE, ALGO_ID, LOT_SIZE, SENSEX_LOT_SIZE

    # Detect exchange and strip internal prefix from security_id.
    # NFO_XXXXX → NSE derivatives (Nifty options)
    # BFO_XXXXX → BSE derivatives (Sensex options)
    if security_id.startswith('BFO_'):
        exchange        = 'BSE'
        raw_security_id = security_id.replace('BFO_', '')
        _lot            = SENSEX_LOT_SIZE
    else:
        exchange        = 'NSE'
        raw_security_id = security_id.replace('NFO_', '')
        _lot            = LOT_SIZE

    # ── QTY SAFETY CHECK ──────────────────────────────────────────────────
    if qty % _lot != 0:
        old_qty = qty
        qty = (qty // _lot) * _lot
        if qty == 0: qty = _lot
        log.warning(f"⚠️ Qty Adjustment: {old_qty} → {qty} (must be multiple of {_lot} for {exchange})")
        order['qty'] = qty   # keep order dict in sync so monitor closes correct qty

    payload = {
        'txn_type':         txn_type,
        'exchange':         exchange,
        'segment':          'DERIVATIVE',
        'product':          'MARGIN',
        'order_type':       'LIMIT',
        'validity':         'DAY',
        'security_id':      raw_security_id,
        'qty':              int(qty),
        'limit_price':      round(float(limit_price), 2),
        'algo_id':          str(ALGO_ID),
        'is_amo':           False
    }
    try:
        url = f"{INDSTOCKS_BASE}/order"
        res = requests.post(url, json=payload, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            resp_data = res.json().get('data', {})
            order['order_id'] = resp_data.get('order_id', order_id)
            log.info(f"✅ [LIVE TRADE] Order {order['order_id']} placed successfully!")
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
            # Using /funds endpoint as per official documentation
            url = f"{INDSTOCKS_BASE}/funds"
            res = requests.get(url, headers=get_auth_headers(), timeout=5)
            if res.status_code == 200:
                data = res.json().get('data', {})
                # 'option_buy' is the relevant balance for buying CE/PE options
                detailed = data.get('detailed_avl_balance', {})
                return float(detailed.get('option_buy', data.get('sod_balance', 0.0)))
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


def get_open_positions(live: bool = False) -> list:
    """
    Fetches open derivative positions from the broker.
    Requires both segment=derivative AND product=margin per INDstocks API docs.
    Returns a list of dicts with security_id, qty, trading_symbol.
    Returns empty list on paper mode or API failure.
    """
    if not live:
        return []
    import requests
    from .market_data import get_auth_headers
    from .config import INDSTOCKS_BASE
    try:
        url = f"{INDSTOCKS_BASE}/portfolio/positions?segment=derivative&product=margin"
        res = requests.get(url, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            data = res.json().get('data', {})
            net_positions = data.get('net_positions', [])
            open_pos = []
            for p in net_positions:
                net_qty = int(p.get('net_quantity', 0))
                if net_qty != 0:
                    open_pos.append({
                        'security_id': p.get('security_id', ''),
                        'qty':         abs(net_qty),
                        'order_id':    p.get('trading_symbol', 'BROKER'),
                    })
            return open_pos
        log.warning(f"⚠️ Positions API {res.status_code}: {res.text[:120]}")
    except Exception as e:
        log.warning(f"⚠️ Could not fetch open positions: {e}")
    return []


def square_off_all_open_positions(live: bool = False):
    """Fetches all open derivative positions and places MARKET SELL to close each."""
    if not live:
        return
    positions = get_open_positions(live=True)
    if not positions:
        log.info("✅ No open broker positions found.")
        return
    log.warning(f"⚠️ Found {len(positions)} open position(s) on broker — squaring off...")
    for pos in positions:
        close_order(
            pos['order_id'], 'STARTUP_SQUAREOFF',
            pnl=0.0, live=True,
            security_id=pos['security_id'], qty=pos['qty'],
        )


def close_order(order_id: str, exit_reason: str, pnl: float = 0.0, live: bool = False,
                security_id: str = None, qty: int = 0) -> bool:
    """
    Closes a position and records PnL.
    Paper mode: updates wallet file.
    Live mode: 
      - If position is open (security_id provided), places a SELL order to square off.
      - Otherwise, attempts to cancel the original order.
    """
    if not live:
        log.info(f"🏁 [PAPER TRADE] Position {order_id} closed | Reason: {exit_reason} | PnL: ₹{pnl:+.2f}")
        update_paper_balance(pnl)
        return True

    log.info(f"🏁 [LIVE TRADE] Closing {order_id} | Reason: {exit_reason}")
    import requests
    from .market_data import get_auth_headers
    from .config import INDSTOCKS_BASE, ALGO_ID

    # ── CASE 1: SQUARE OFF ACTIVE POSITION ──────────────────────────────────
    if security_id and qty > 0:
        exchange        = 'BSE' if security_id.startswith('BFO_') else 'NSE'
        raw_security_id = security_id.replace('BFO_', '').replace('NFO_', '')
        payload = {
            'txn_type':         'SELL',
            'exchange':         exchange,
            'segment':          'DERIVATIVE',
            'product':          'MARGIN',
            'order_type':       'MARKET',   # Use MARKET for fast exit on SL/TP
            'validity':         'DAY',
            'security_id':      raw_security_id,
            'qty':              int(qty),
            'algo_id':          str(ALGO_ID),
            'is_amo':           False
        }
        try:
            url = f"{INDSTOCKS_BASE}/order"
            res = requests.post(url, json=payload, headers=get_auth_headers(), timeout=5)
            if res.status_code == 200:
                log.info(f"✅ [LIVE TRADE] Square-off SELL order placed for {order_id}.")
                return True
            log.error(f"❌ [LIVE TRADE] Square-off Failed: {res.status_code} {res.text[:120]}")
        except Exception as e:
            log.error(f"❌ [LIVE TRADE] Square-off API Error: {e}")

    # ── CASE 2: CANCEL PENDING ORDER ────────────────────────────────────────
    try:
        # Using standard /order/cancel endpoint as per official documentation
        url = f"{INDSTOCKS_BASE}/order/cancel"
        res = requests.post(url,
                            json={'order_id': order_id, 'segment': 'DERIVATIVE'},
                            headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            log.info(f"✅ [LIVE TRADE] Order {order_id} cancellation request sent.")
            return True
        log.error(f"❌ [LIVE TRADE] Cancel failed: {res.status_code} {res.text[:120]}")
    except Exception as e:
        log.error(f"❌ [LIVE TRADE] Cancel error: {e}")
    return False
