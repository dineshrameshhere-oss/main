import time
from typing import Callable
from .logger import log
from .trade_executor import close_order
from .config import DEFAULT_SL_PCT, MAX_DAILY_LOSS_PCT, TRAILING_STEPS, BROKERAGE_PER_TRADE

# Max retries when LTP fetch returns zero (API hiccup)
_LTP_ZERO_MAX_RETRIES = 5


def _get_stepped_sl_floor(pnl_pct: float) -> float | None:
    """
    Returns the locked SL floor (% of entry premium) for the current profit level.
    Walks TRAILING_STEPS from highest trigger downward.
    Returns None if no step triggered yet (hard SL still applies).
    """
    for trigger, floor in reversed(TRAILING_STEPS):
        if pnl_pct >= trigger:
            return floor
    return None


def _fetch_live_premium(security_id: str) -> float:
    """
    Fetches the live option LTP from INDMoney.
    Works for both PAPER and LIVE modes — same API, same security ID (NFO_XXXXX).
    Returns 0.0 on failure.
    """
    try:
        import requests
        from .market_data import get_auth_headers
        from .config import INDSTOCKS_BASE

        url = f"{INDSTOCKS_BASE}/market/quotes/ltp?scrip-codes={security_id}"
        res = requests.get(url, headers=get_auth_headers(), timeout=4)
        if res.status_code == 200:
            data = res.json()
            return float(data.get('data', {}).get(security_id, {}).get('live_price', 0))
        else:
            log.warning(f"LTP API {res.status_code}: {res.text[:80]}")
    except Exception as e:
        log.warning(f"LTP fetch error: {e}")
    return 0.0


def monitor_position(order: dict, live: bool = False,
                     on_close: Callable | None = None):
    """
    Monitors an active trade every 30 seconds.
    Fetches REAL live LTP from INDMoney API for BOTH paper and live modes.
    Calls on_close() when the trade is fully closed so the scheduler
    can clear active_position and accept the next signal.

    Exit conditions:
      1. Stepped trailing SL hit  → TRAILING_SL_STEP
      2. Hard SL hit              → HARD_SL
      3. Daily loss circuit       → DAILY_LOSS_LIMIT
      4. 60-minute max hold       → TIME_LIMIT_EXIT
    """
    if not order:
        return

    order_id    = order['order_id']
    security_id = order.get('security_id', '')
    entry       = float(order['entry_price'])    # option premium at entry
    qty         = int(order.get('qty', 25))
    hard_sl     = float(order['sl_price'])
    initial_tp  = float(order['tp_price'])

    # Guard: if security ID is a dummy (no live feed possible), log and exit cleanly
    if 'DUMMY' in security_id.upper():
        log.warning(
            f"[{order_id}] Security ID is a dummy ({security_id}). "
            f"Cannot monitor — no live LTP available."
        )
        if on_close: on_close(0.0)
        return

    # Tracking state
    peak_pnl_pct     = 0.0
    current_sl_floor = -DEFAULT_SL_PCT   # starts as hard SL (negative = loss allowed)
    zero_ltp_retries = 0
    tick             = 0
    max_ticks        = 180               # 180 × 10s = 30 min max hold
    tp_logged        = False

    log.info(
        f"Monitor START [{order_id}] | Entry ₹{entry:.2f} | "
        f"Hard SL ₹{hard_sl:.2f} | Initial TP ₹{initial_tp:.2f} | "
        f"Mode: {'LIVE' if live else 'PAPER'} | Poll: 10s | Max hold: 30 min"
    )
    log.info(f"Stepped Trailing SL active — {len(TRAILING_STEPS)} rungs up to +200%")

    while tick < max_ticks:
        time.sleep(10)     # poll every 10s — captures intraday volatility spikes
        tick += 1

        # ── Fetch live LTP ────────────────────────────────────────────────────
        current_premium = _fetch_live_premium(security_id)

        if current_premium <= 0:
            zero_ltp_retries += 1
            log.warning(f"[{order_id}] Zero LTP (retry {zero_ltp_retries}/{_LTP_ZERO_MAX_RETRIES}) | {security_id}")
            if zero_ltp_retries >= _LTP_ZERO_MAX_RETRIES:
                log.error(f"[{order_id}] LTP unavailable after {_LTP_ZERO_MAX_RETRIES} retries — force-closing.")
                close_order(order_id, "LTP_UNAVAILABLE", pnl=0.0, live=live, 
                            security_id=security_id, qty=qty)
                if on_close: on_close(0.0)
                return
            continue
        zero_ltp_retries = 0

        # ── P&L calculation ───────────────────────────────────────────────────
        pnl_pct    = (current_premium - entry) / entry
        pnl_amount = (current_premium - entry) * qty

        # ── Update peak & ratchet SL floor ───────────────────────────────────
        if pnl_pct > peak_pnl_pct:
            peak_pnl_pct = pnl_pct
            new_floor = _get_stepped_sl_floor(peak_pnl_pct)
            if new_floor is not None and new_floor > current_sl_floor:
                # Dynamic brokerage floor: locked PnL must cover ₹50 round-trip cost
                # floor_pct = brokerage / (entry_premium * qty)
                brokerage_floor = BROKERAGE_PER_TRADE / (entry * qty)
                enforced_floor  = max(new_floor, brokerage_floor)

                old_floor        = current_sl_floor
                current_sl_floor = enforced_floor
                sl_locked_price  = entry * (1 + current_sl_floor)
                log.info(
                    f"🔒 STEP-UP [{order_id}] | Peak +{peak_pnl_pct*100:.1f}% | "
                    f"SL floor: {old_floor*100:+.1f}% → {current_sl_floor*100:+.1f}% "
                    f"(₹{sl_locked_price:.2f}) | covers brokerage: "
                    f"{'YES' if current_sl_floor >= brokerage_floor else 'NO'}"
                )

        effective_sl = entry * (1 + current_sl_floor)

        # ── Progress log every 30s (every 3 ticks at 10s) — reduce noise ──────
        if tick % 3 == 0:
            arrow = "📈" if pnl_pct >= 0 else "📉"
            log.info(
                f"{arrow} [{order_id}] LTP ₹{current_premium:.2f} | "
                f"PnL {pnl_pct*100:+.1f}% (₹{pnl_amount:+.0f}) | "
                f"Peak {peak_pnl_pct*100:.1f}% | SL floor {current_sl_floor*100:+.1f}% (₹{effective_sl:.2f})"
            )

        # ── 1. INITIAL TP NOTIFICATION (let winner run past it) ──────────────
        if not tp_logged and current_premium >= initial_tp:
            tp_logged = True
            log.info(
                f"🎯 TP CROSSED [{order_id}] ₹{current_premium:.2f} (+{pnl_pct*100:.1f}%) "
                f"— trailing SL active, letting winner run!"
            )

        # ── 2. SL HIT (hard SL or trailing floor) ────────────────────────────
        # Slippage Buffer: Market moves fast. We exit if LTP is AT or BELOW 
        # (effective_sl + 0.1% buffer) to avoid getting filled even lower.
        slippage_buffer = entry * 0.001  # 0.1% buffer
        if current_premium <= (effective_sl + slippage_buffer):
            reason = "TRAILING_SL_STEP" if current_sl_floor > -DEFAULT_SL_PCT else "HARD_SL"
            log.warning(
                f"🛑 {reason} [{order_id}] | Exit ₹{current_premium:.2f} "
                f"| Target ₹{effective_sl:.2f} | Buffer ₹{slippage_buffer:.2f} "
                f"| Locked {current_sl_floor*100:+.0f}% | PnL ₹{pnl_amount:+.0f}"
            )
            close_order(order_id, reason, pnl=pnl_amount, live=live, 
                        security_id=security_id, qty=qty)
            if on_close: on_close(pnl_amount)
            return

        # ── 3. DAILY LOSS CIRCUIT BREAKER ─────────────────────────────────────
        max_loss = entry * qty * MAX_DAILY_LOSS_PCT
        if pnl_amount < -max_loss:
            log.warning(
                f"🚨 DAILY LOSS LIMIT [{order_id}] | Loss ₹{abs(pnl_amount):.0f} "
                f"exceeds {MAX_DAILY_LOSS_PCT*100:.0f}% circuit breaker."
            )
            close_order(order_id, "DAILY_LOSS_LIMIT", pnl=pnl_amount, live=live, 
                        security_id=security_id, qty=qty)
            if on_close: on_close(pnl_amount)
            return

    # ── 4. 60-MINUTE MAX HOLD REACHED ─────────────────────────────────────────
    final_premium = _fetch_live_premium(security_id)
    if final_premium <= 0:
        final_premium = entry   # last-resort fallback
    final_pnl = (final_premium - entry) * qty
    log.info(
        f"⏰ TIME LIMIT [{order_id}] | 60 min elapsed | "
        f"Exit ₹{final_premium:.2f} | PnL ₹{final_pnl:+.0f}"
    )
    close_order(order_id, "TIME_LIMIT_EXIT", pnl=final_pnl, live=live, 
                security_id=security_id, qty=qty)
    if on_close: on_close(final_pnl)
