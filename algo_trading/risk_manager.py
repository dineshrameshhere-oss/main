import time
from .logger import log
from .trade_executor import close_order
from .config import DEFAULT_SL_PCT, MAX_DAILY_LOSS_PCT, TRAILING_STEPS

# Max retries when LTP fetch returns zero (API hiccup)
_LTP_ZERO_MAX_RETRIES = 3


def _get_stepped_sl_floor(pnl_pct: float) -> float | None:
    """
    Returns the locked SL floor (as % of entry premium) for the current profit level.

    Walks the TRAILING_STEPS ladder from highest trigger downward and returns
    the first floor whose trigger has been reached.  Returns None if no step
    has been triggered yet (hard SL still applies).

    Example:
        pnl_pct = 0.35  ->  trigger 0.30 matched  ->  floor = +0.15
        pnl_pct = 0.08  ->  no trigger matched    ->  None  (use original SL)
    """
    for trigger, floor in reversed(TRAILING_STEPS):
        if pnl_pct >= trigger:
            return floor
    return None


def _fetch_live_premium(security_id: str) -> float:
    """
    Fetches the live option premium (LTP) from INDMoney.
    Returns 0.0 on failure — caller handles retry logic.
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
            log.warning(f"LTP API {res.status_code}: {res.text[:120]}")
    except Exception as e:
        log.warning(f"LTP fetch error: {e}")
    return 0.0


def monitor_position(order: dict, live: bool = False):
    """
    Monitors an active trade for Stepped Trailing SL, Hard TP, or Daily Loss limit.
    Optimised for fast 5-10 min scalps.

    Stepped Trailing SL Ladder (from config.TRAILING_STEPS):
    ─────────────────────────────────────────────────────────
      At +10% premium gain  →  SL floor moves to +5%
      At +20% premium gain  →  SL floor moves to +10%
      ...
      At +100% premium gain →  SL floor moves to +50%
      At +200% premium gain →  SL floor moves to +100%

    The SL floor is always expressed as a % of entry_premium.
    It only moves UP — once a milestone is hit, the floor never drops back.

    LIVE mode : polls real option LTP from INDMoney every 5 s.
    PAPER mode: clock-based 15-min max hold; SL/TP managed by backtest engine.
    """
    if not order:
        return

    order_id      = order['order_id']
    security_id   = order.get('security_id', '')
    entry         = order['entry_price']        # option premium at entry
    qty           = order['qty']
    start_capital = order.get('capital_at_entry', entry * qty)

    # Hard initial SL — stays as absolute floor until trailing kicks in
    hard_sl_price = order['sl_price']           # = entry * (1 - DEFAULT_SL_PCT)
    hard_tp_price = order['tp_price']           # initial TP (no longer a hard limit — just log)

    # Tracking state
    peak_pnl_pct       = 0.0                    # highest profit % reached
    current_sl_floor   = -DEFAULT_SL_PCT        # starts as original SL (negative = loss)
    zero_ltp_retries   = 0
    max_paper_ticks    = 180                    # paper: 15 min max hold
    max_loss_amount    = start_capital * MAX_DAILY_LOSS_PCT

    log.info(
        f"Monitor START [{order_id}] | Entry ₹{entry:.2f} | Hard SL ₹{hard_sl_price:.2f} "
        f"| Initial TP ₹{hard_tp_price:.2f} | Mode: {'LIVE' if live else 'PAPER'}"
    )
    log.info(f"Stepped Trailing SL active — {len(TRAILING_STEPS)} rungs up to +200%")

    tick = 0
    while True:
        tick += 1

        # ── Fetch current premium ──────────────────────────────────────────────
        if live:
            current_premium = _fetch_live_premium(security_id)
            if current_premium <= 0:
                zero_ltp_retries += 1
                log.warning(f"Zero LTP (retry {zero_ltp_retries}/{_LTP_ZERO_MAX_RETRIES})")
                if zero_ltp_retries >= _LTP_ZERO_MAX_RETRIES:
                    log.error("LTP unavailable — force-closing for safety.")
                    close_order(order_id, "LTP_UNAVAILABLE", pnl=0.0, live=live)
                    break
                time.sleep(5)
                continue
            zero_ltp_retries = 0
        else:
            # Paper mode: no live feed — rely on backtest engine for SL/TP
            if tick > max_paper_ticks:
                log.info("Paper trade max hold reached — force exit at entry price.")
                close_order(order_id, "PAPER_TIME_LIMIT", pnl=0.0, live=False)
                break
            time.sleep(5)
            continue

        # ── P&L ───────────────────────────────────────────────────────────────
        pnl_pct    = (current_premium - entry) / entry
        pnl_amount = (current_premium - entry) * qty

        # ── Update peak & SL floor (ratchet — never drop) ────────────────────
        if pnl_pct > peak_pnl_pct:
            peak_pnl_pct = pnl_pct

            # Find the highest triggered step for the new peak
            new_floor = _get_stepped_sl_floor(peak_pnl_pct)
            if new_floor is not None and new_floor > current_sl_floor:
                old_floor = current_sl_floor
                current_sl_floor = new_floor
                sl_price_locked = entry * (1 + current_sl_floor)
                log.info(
                    f"STEP-UP [{order_id}] | Peak +{peak_pnl_pct*100:.1f}% | "
                    f"SL floor: +{old_floor*100:.0f}% -> +{current_sl_floor*100:.0f}% "
                    f"(₹{sl_price_locked:.2f})"
                )

        # Current effective SL price
        effective_sl = entry * (1 + current_sl_floor)

        log.debug(
            f"[{order_id}] LTP ₹{current_premium:.2f} | PnL {pnl_pct*100:+.2f}% "
            f"| Peak {peak_pnl_pct*100:.1f}% | SL floor +{current_sl_floor*100:.0f}% "
            f"(₹{effective_sl:.2f})"
        )

        # ── 1. STEPPED TRAILING SL HIT ────────────────────────────────────────
        if current_premium <= effective_sl:
            reason = "TRAILING_SL_STEP" if current_sl_floor >= 0 else "HARD_SL"
            log.warning(
                f"{reason} HIT [{order_id}] | Exit ₹{current_premium:.2f} "
                f"| Locked floor was +{current_sl_floor*100:.0f}% | PnL ₹{pnl_amount:.2f}"
            )
            close_order(order_id, reason, pnl=pnl_amount, live=live)
            break

        # ── 2. LOG WHEN HARD TP IS FIRST CROSSED (no longer auto-exit) ───────
        # With the trailing ladder, we let winners run past original TP.
        # We log it so the user can see when it's crossed.
        if current_premium >= hard_tp_price and pnl_pct < 0.21:
            log.info(
                f"TP CROSSED [{order_id}] ₹{current_premium:.2f} (+{pnl_pct*100:.1f}%) "
                f"— trailing SL now active, letting winner run."
            )

        # ── 3. DAILY LOSS CIRCUIT BREAKER ────────────────────────────────────
        if pnl_amount < -max_loss_amount:
            log.warning(
                f"DAILY LOSS LIMIT [{order_id}] | Loss ₹{abs(pnl_amount):.2f} "
                f"exceeds {MAX_DAILY_LOSS_PCT*100:.0f}% of day capital."
            )
            close_order(order_id, "DAILY_LOSS_LIMIT", pnl=pnl_amount, live=live)
            break

        time.sleep(5)
