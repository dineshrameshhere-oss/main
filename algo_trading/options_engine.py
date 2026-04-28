from .logger import log
from .config import LOT_SIZE, MAX_TRADE_BUDGET
from .market_data import fetch_ltp, get_auth_headers
from .config import INDSTOCKS_BASE
import requests
import pandas as pd
import io
import datetime

# ─────────────────────────────────────────────────────────────────────────────
#  INSTRUMENTS CACHE
#  Loaded once per session from INDMoney FNO instruments CSV
# ─────────────────────────────────────────────────────────────────────────────
_INSTRUMENTS_DF = None

def _get_instruments() -> pd.DataFrame:
    global _INSTRUMENTS_DF
    if _INSTRUMENTS_DF is not None:
        return _INSTRUMENTS_DF
    try:
        url = f"{INDSTOCKS_BASE}/market/instruments?source=fno"
        res = requests.get(url, headers=get_auth_headers(), timeout=10)
        if res.status_code == 200:
            _INSTRUMENTS_DF = pd.read_csv(io.StringIO(res.text))
            _INSTRUMENTS_DF['EXPIRY_DATE'] = pd.to_datetime(
                _INSTRUMENTS_DF['EXPIRY_DATE'], errors='coerce'
            )
            log.info(f"Instruments loaded: {len(_INSTRUMENTS_DF)} rows")
            return _INSTRUMENTS_DF
    except Exception as e:
        log.error(f"Error fetching instruments: {e}")
    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
#  NIFTY 50 SPOT PRICE
#  NSE_3045 returns ~1107 (a scaled index value, not real Nifty 50).
#  We derive the real spot from the ATM option's own strike instead,
#  or fall back to a known good scrip if available.
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nifty_spot() -> float:
    """
    Returns the real Nifty 50 spot price by finding the nearest-to-the-money
    ATM strike from the live options chain and using it as a proxy.
    Falls back to 24400 if all else fails.
    """
    try:
        df = _get_instruments()
        if df.empty:
            return 24400.0

        today = pd.to_datetime(datetime.date.today())
        nifty_opts = df[
            df['TRADING_SYMBOL'].str.upper().str.startswith('NIFTY', na=False) &
            (df['INSTRUMENT_NAME'] == 'OPTIDX')
        ].copy()

        future = nifty_opts[nifty_opts['EXPIRY_DATE'] >= today]
        if future.empty:
            return 24400.0

        near_exp = future['EXPIRY_DATE'].min()
        near_opts = future[future['EXPIRY_DATE'] == near_exp]

        # Fetch LTP for a range of CE strikes to find the one closest to ATM
        # (ATM CE and PE have roughly equal premiums — we use CE around 24000-25500)
        candidate_strikes = sorted(near_opts[
            (near_opts['OPTION_TYPE'] == 'CE') &
            (near_opts['STRIKE_PRICE'] >= 22000) &
            (near_opts['STRIKE_PRICE'] <= 26000)
        ]['STRIKE_PRICE'].unique())

        if not candidate_strikes:
            return 24400.0

        # Binary-search style: the ATM strike is where CE ≈ PE premium
        # Quick proxy: fetch a few CE LTPs and the ATM is near where premium ≈ 100-200
        best_strike = None
        for strike in candidate_strikes[::5]:  # sample every 5th strike
            row = near_opts[
                (near_opts['STRIKE_PRICE'] == strike) &
                (near_opts['OPTION_TYPE'] == 'CE')
            ]
            if row.empty:
                continue
            sec_id = f"NFO_{int(row.iloc[0]['SECURITY_ID'])}"
            ltp = _fetch_option_ltp_raw(sec_id)
            if 50 <= ltp <= 500:   # ATM premiums in this range
                best_strike = strike

        if best_strike:
            return float(best_strike)

    except Exception as e:
        log.warning(f"fetch_nifty_spot error: {e}")

    return 24400.0


# ─────────────────────────────────────────────────────────────────────────────
#  OPTION LTP  (raw helper)
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_option_ltp_raw(scrip_code: str) -> float:
    """Fetches live premium. scrip_code must be in format 'NFO_<SECURITY_ID>'."""
    try:
        url = f"{INDSTOCKS_BASE}/market/quotes/ltp?scrip-codes={scrip_code}"
        res = requests.get(url, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            data = res.json()
            return float(data.get('data', {}).get(scrip_code, {}).get('live_price', 0))
    except Exception as e:
        log.warning(f"LTP fetch error for {scrip_code}: {e}")
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  SECURITY ID LOOKUP
# ─────────────────────────────────────────────────────────────────────────────
def get_security_id_for_option(strike: float, opt_type: str, expiry: pd.Timestamp = None) -> str:
    """
    Returns 'NFO_<SECURITY_ID>' for the given NIFTY strike/type.
    Uses nearest expiry if expiry not specified.
    """
    df = _get_instruments()
    if df.empty:
        return f"NFO_DUMMY_{int(strike)}_{opt_type}"

    try:
        today = pd.to_datetime(datetime.date.today())
        nifty_opts = df[
            df['TRADING_SYMBOL'].str.upper().str.startswith('NIFTY', na=False) &
            (df['INSTRUMENT_NAME'] == 'OPTIDX') &
            (df['OPTION_TYPE'] == opt_type)
        ].copy()

        future = nifty_opts[nifty_opts['EXPIRY_DATE'] >= today]
        if future.empty:
            future = nifty_opts

        target_expiry = expiry if expiry else future['EXPIRY_DATE'].min()
        specific = future[
            (future['EXPIRY_DATE'] == target_expiry) &
            (future['STRIKE_PRICE'] == float(strike))
        ]

        if not specific.empty:
            sec_id = int(specific.iloc[0]['SECURITY_ID'])
            return f"NFO_{sec_id}"

    except Exception as e:
        log.error(f"Security ID lookup error: {e}")

    return f"NFO_DUMMY_{int(strike)}_{opt_type}"


# ─────────────────────────────────────────────────────────────────────────────
#  SELECT BEST STRIKE  (core function called by scheduler)
# ─────────────────────────────────────────────────────────────────────────────
def select_strike(direction: str, spot_price: float, budget: float) -> dict:
    """
    Finds the best affordable NIFTY option strike with a real live premium.

    Strategy:
    - Always 1 lot (25 qty)
    - Buy the highest-delta (most expensive) option we can afford
    - Check real live LTPs from NFO_<security_id>
    - Round trip: instruments CSV → security ID → NFO quote LTP

    Returns dict with security_id, strike, type, simulated_premium.
    """
    opt_type = "CE" if "LONG" in direction else "PE"

    # Ensure spot is real Nifty 50 level
    if spot_price < 10000:
        log.info(f"Spot {spot_price} looks wrong — fetching real Nifty spot...")
        spot_price = fetch_nifty_spot()

    # ATM rounded to nearest 50
    atm = round(spot_price / 50) * 50

    # Build strike list: ATM ± 4 strikes (200 pts range)
    strikes = [atm + (i * 50) for i in range(-4, 5)]  # 9 strikes

    log.info(f"Searching {opt_type} strikes around ATM {atm} (spot {spot_price:.0f}) | Budget Rs {budget:.0f}")

    df = _get_instruments()
    today = pd.to_datetime(datetime.date.today())

    best_strike   = atm
    best_premium  = 0.0
    best_sec_id   = ""
    near_expiry   = None

    if not df.empty:
        nifty_opts = df[
            df['TRADING_SYMBOL'].str.upper().str.startswith('NIFTY', na=False) &
            (df['INSTRUMENT_NAME'] == 'OPTIDX') &
            (df['OPTION_TYPE'] == opt_type) &
            (df['EXPIRY_DATE'] >= today)
        ].copy()

        if not nifty_opts.empty:
            near_expiry = nifty_opts['EXPIRY_DATE'].min()
            near_opts = nifty_opts[nifty_opts['EXPIRY_DATE'] == near_expiry]

            for strike in strikes:
                row = near_opts[near_opts['STRIKE_PRICE'] == float(strike)]
                if row.empty:
                    continue

                sec_id = f"NFO_{int(row.iloc[0]['SECURITY_ID'])}"
                ltp    = _fetch_option_ltp_raw(sec_id)

                if ltp <= 0:
                    continue

                cost = ltp * LOT_SIZE
                log.info(f"  {strike} {opt_type}: Rs {ltp:.2f}/unit | Cost Rs {cost:.0f} | {sec_id}")

                if cost <= budget and ltp > best_premium:
                    best_premium = ltp
                    best_strike  = strike
                    best_sec_id  = sec_id

    if best_premium > 0:
        log.info(f"✅ Best strike: NIFTY {best_strike} {opt_type} @ Rs {best_premium:.2f} | {best_sec_id}")
    else:
        log.warning("⚠️ Could not find live option premium. Check token or market hours.")
        # Hard fallback — no dummy, just return zero premium so trade is skipped
        return {
            "security_id": f"NFO_DUMMY_{int(atm)}_{opt_type}",
            "strike":      atm,
            "type":        opt_type,
            "simulated_premium": 0.0   # caller checks qty == 0 when premium is 0
        }

    return {
        "security_id":       best_sec_id,
        "strike":            best_strike,
        "type":              opt_type,
        "simulated_premium": best_premium
    }


# ─────────────────────────────────────────────────────────────────────────────
#  RISK SIZING
# ─────────────────────────────────────────────────────────────────────────────
def calculate_dynamic_risk(premium: float):
    """
    Scales SL/TP based on option premium quality (proxy for Delta).
    Higher premium = lower Delta volatility = tighter levels.
    """
    if premium < 80:
        return 0.10, 0.20   # Deep OTM — wide SL, wide TP
    elif premium < 180:
        return 0.08, 0.16   # ATM-ish
    else:
        return 0.05, 0.12   # ITM — tighter, faster


def calculate_qty(budget: float, option_premium: float, lot_size: int = LOT_SIZE):
    """Always exactly 1 lot. Returns (qty, cost, lots)."""
    if option_premium <= 0:
        return 0, 0, 0
    cost = option_premium * lot_size
    if budget < cost:
        log.warning(f"Budget Rs {budget:.0f} < cost Rs {cost:.0f} for 1 lot @ Rs {option_premium:.2f}")
        return 0, 0, 0
    return lot_size, cost, 1


# ─────────────────────────────────────────────────────────────────────────────
#  LEGACY shim — kept for any imports still using old name
# ─────────────────────────────────────────────────────────────────────────────
def get_instruments():
    return _get_instruments()

def fetch_option_ltp(security_id: str) -> float:
    return _fetch_option_ltp_raw(security_id)
