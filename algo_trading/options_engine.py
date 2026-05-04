from .logger import log
from . import config
from .config import INDSTOCKS_BASE, PCR_BULLISH_MAX, PCR_BEARISH_MIN
from .market_data import fetch_ltp, get_auth_headers
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
            
            # ── DYNAMIC LOT SIZE EXTRACTION ─────────────────────────────────────
            # Clean column names
            _INSTRUMENTS_DF.columns = _INSTRUMENTS_DF.columns.str.strip().str.upper()
            
            _INSTRUMENTS_DF['EXPIRY_DATE'] = pd.to_datetime(
                _INSTRUMENTS_DF['EXPIRY_DATE'], errors='coerce'
            )
            log.info(f"Instruments loaded: {len(_INSTRUMENTS_DF)} rows")
            
            # Debug: Log column names to find the right lot size column
            log.debug(f"Instrument columns: {list(_INSTRUMENTS_DF.columns)}")

            # Check for LOT_SIZE column
            # INDMoney typically uses 'LOT_SIZE' or 'MIN_LOT_QUANTITY'
            lot_col = next((c for c in ['LOT_SIZE', 'MIN_LOT_QUANTITY', 'FREEZE_QTY'] if c in _INSTRUMENTS_DF.columns), None)
            
            if lot_col:
                # Filter for NIFTY OPTIDX
                nifty_mask = (
                    _INSTRUMENTS_DF['TRADING_SYMBOL'].str.upper().str.startswith('NIFTY', na=False) &
                    (_INSTRUMENTS_DF['INSTRUMENT_NAME'] == 'OPTIDX')
                )
                nifty_sample = _INSTRUMENTS_DF[nifty_mask]
                
                if not nifty_sample.empty:
                    dynamic_lot = int(nifty_sample.iloc[0][lot_col])
                    if dynamic_lot > 0:
                        config.LOT_SIZE = dynamic_lot
                        log.info(f"📊 DYNAMIC LOT SIZE DETECTED: {config.LOT_SIZE} (from {lot_col})")
                else:
                    log.warning("⚠️ Dynamic Lot Size: Could not find NIFTY OPTIDX row in instruments.")
            else:
                log.warning(f"⚠️ Dynamic Lot Size: Could not find lot column in instruments. Columns: {list(_INSTRUMENTS_DF.columns)}")

            return _INSTRUMENTS_DF
    except Exception as e:
        log.error(f"Error fetching instruments: {e}")
    return pd.DataFrame()


def compute_pcr() -> dict:
    """
    Computes Put-Call Ratio from near-expiry NIFTY options chain OI.

    Uses the instruments CSV (already loaded/cached) — no extra API calls.
    OI column = 'OPEN_INTEREST' if present, otherwise falls back to 1 per row.

    Returns:
        {
            'pcr':       float  (total put OI / total call OI),
            'bias':      str    ('BULLISH' | 'BEARISH' | 'NEUTRAL'),
            'put_oi':    int,
            'call_oi':   int,
        }
    """
    try:
        df = _get_instruments()
        if df.empty:
            return {'pcr': 1.0, 'bias': 'NEUTRAL', 'put_oi': 0, 'call_oi': 0}

        today = pd.to_datetime(datetime.date.today())
        nifty_opts = df[
            df['TRADING_SYMBOL'].str.upper().str.startswith('NIFTY', na=False) &
            (df['INSTRUMENT_NAME'] == 'OPTIDX') &
            (df['EXPIRY_DATE'] >= today)
        ].copy()

        if nifty_opts.empty:
            return {'pcr': 1.0, 'bias': 'NEUTRAL', 'put_oi': 0, 'call_oi': 0}

        near_expiry = nifty_opts['EXPIRY_DATE'].min()
        near_opts   = nifty_opts[nifty_opts['EXPIRY_DATE'] == near_expiry]

        # Use OPEN_INTEREST if available, else count rows as proxy
        oi_col = 'OPEN_INTEREST' if 'OPEN_INTEREST' in near_opts.columns else None
        if oi_col:
            put_oi  = float(near_opts[near_opts['OPTION_TYPE'] == 'PE'][oi_col].sum())
            call_oi = float(near_opts[near_opts['OPTION_TYPE'] == 'CE'][oi_col].sum())
        else:
            put_oi  = float(len(near_opts[near_opts['OPTION_TYPE'] == 'PE']))
            call_oi = float(len(near_opts[near_opts['OPTION_TYPE'] == 'CE']))

        if call_oi == 0:
            return {'pcr': 1.0, 'bias': 'NEUTRAL', 'put_oi': 0, 'call_oi': 0}

        pcr = round(put_oi / call_oi, 3)

        if pcr > PCR_BEARISH_MIN:
            bias = 'BEARISH'
        elif pcr < PCR_BULLISH_MAX:
            bias = 'BULLISH'
        else:
            bias = 'NEUTRAL'

        return {'pcr': pcr, 'bias': bias, 'put_oi': int(put_oi), 'call_oi': int(call_oi)}

    except Exception as e:
        log.warning(f"PCR compute error: {e}")
        return {'pcr': 1.0, 'bias': 'NEUTRAL', 'put_oi': 0, 'call_oi': 0}



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
    Finds a 'Quality Premium' NIFTY option strike (Delta 0.35 - 0.55).
    
    Strategy:
    - Prioritize liquidity and premium sensitivity over 'cheapness'.
    - Target Delta range: 0.35 to 0.55 (Shallow OTM to ATM).
    - If budget allows, buy the highest quality (closest to ATM) strike.
    - If budget is tight, buy the best affordable strike in the range.

    Returns dict with security_id, strike, type, simulated_premium.
    """
    from .indicators import compute_greeks
    opt_type = "CE" if "LONG" in direction else "PE"

    # Ensure spot is real Nifty 50 level
    if spot_price < 10000:
        log.info(f"Spot {spot_price} looks wrong — fetching real Nifty spot...")
        spot_price = fetch_nifty_spot()

    # ATM rounded to nearest 50
    atm = round(spot_price / 50) * 50

    # Build strike list: ATM ± 5 strikes (250 pts range)
    # We look further OTM if budget is tight, and further ITM if we want quality.
    strikes = [atm + (i * 50) for i in range(-5, 6)]

    log.info(f"Searching Quality {opt_type} strikes around ATM {atm} | Budget Rs {budget:.0f}")

    df = _get_instruments()
    today = pd.to_datetime(datetime.date.today())

    candidates = []
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
            days_to_exp = max(1, (near_expiry.date() - datetime.date.today()).days)

            for strike in strikes:
                row = near_opts[near_opts['STRIKE_PRICE'] == float(strike)]
                if row.empty: continue

                sec_id = f"NFO_{int(row.iloc[0]['SECURITY_ID'])}"
                ltp    = _fetch_option_ltp_raw(sec_id)
                if ltp <= 0: continue

                # Calculate Delta to verify 'Quality'
                greeks = compute_greeks(spot_price, strike, days_to_exp, ltp, opt_type)
                delta = greeks['delta']
                cost = ltp * config.LOT_SIZE

                log.info(f"  {strike} {opt_type}: Rs {ltp:.2f} | Delta {delta:.2f} | Cost Rs {cost:.0f}")

                if cost <= budget:
                    candidates.append({
                        'strike': strike,
                        'premium': ltp,
                        'sec_id': sec_id,
                        'delta': delta,
                        'days_to_exp': days_to_exp
                    })

    # Selection Logic:
    # 1. Preferred range: Delta 0.40 - 0.60 (ATM focus for maximum profitability)
    # 2. Pick the one with HIGHEST delta within this range (closest to ATM)
    # 3. If none in range, pick the one with delta closest to 0.45 (Quality OTM)
    
    best_choice = None
    
    # Filter for preferred quality range
    quality_candidates = [c for c in candidates if 0.40 <= c['delta'] <= 0.65]
    
    if quality_candidates:
        # Pick highest delta (most sensitive/ATM)
        best_choice = max(quality_candidates, key=lambda x: x['delta'])
    elif candidates:
        # Fallback: pick the one closest to 0.45 delta (the 'best' quality we can afford)
        best_choice = max(candidates, key=lambda x: x['delta'])

    if best_choice:
        log.info(f"✅ Quality Match: NIFTY {best_choice['strike']} {opt_type} @ Rs {best_choice['premium']:.2f} (Delta {best_choice['delta']:.2f})")
        return {
            "security_id":       best_choice['sec_id'],
            "strike":            best_choice['strike'],
            "type":              opt_type,
            "days_to_expiry":    best_choice['days_to_exp'],
            "simulated_premium": best_choice['premium']
        }
    else:
        log.warning("⚠️ No affordable quality strikes found.")
        return {
            "security_id":       f"NFO_DUMMY_{int(atm)}_{opt_type}",
            "strike":            atm,
            "type":              opt_type,
            "days_to_expiry":    7,
            "simulated_premium": 0.0
        }


# ─────────────────────────────────────────────────────────────────────────────
#  RISK SIZING
# ─────────────────────────────────────────────────────────────────────────────
def calculate_dynamic_risk(premium: float):
    """
    SL/TP calibrated to OTM momentum scalping.
    TP at 25% — achievable given that on strong momentum days,
    OTM premiums can 2-3x easily (100→250 is 150% gain).
    The trailing SL ladder in config.py takes over once price moves in our favour.
    """
    if premium < 80:
        return 0.10, 0.25   # Deep OTM — SL 10%, TP 25% (needs big move)
    elif premium < 180:
        return 0.08, 0.25   # ATM-ish — SL 8%, TP 25%
    else:
        return 0.05, 0.25   # ITM — tight SL, same TP


def calculate_qty(budget: float, option_premium: float, is_strong_conviction: bool = False, lot_size: int = None):
    """
    Calculates how many lots to buy based on budget and LOT_SCALE_TIERS.
    Only returns multiple lots if is_strong_conviction is True.
    Returns (qty, cost, lots).
    """
    if option_premium <= 0:
        return 0, 0, 0
    
    if lot_size is None:
        lot_size = config.LOT_SIZE

    from .config import LOT_SCALE_TIERS
    cost_per_lot = option_premium * lot_size
    
    # Find max affordable lots based on tier
    max_lots = 1
    if is_strong_conviction:
        for tier_cap, tier_lots in sorted(LOT_SCALE_TIERS, key=lambda x: x[0], reverse=True):
            if budget >= tier_cap:
                max_lots = tier_lots
                break
    
    # Cap at what budget actually allows
    budget_lots = int(budget / cost_per_lot)
    max_lots = min(max_lots, budget_lots)
    
    if max_lots < 1:
        log.warning(f"Budget Rs {budget:.0f} < cost Rs {cost_per_lot:.0f} for 1 lot @ Rs {option_premium:.2f}")
        return 0, 0, 0
    
    qty = max_lots * lot_size
    cost = qty * option_premium
    return qty, cost, max_lots


# ─────────────────────────────────────────────────────────────────────────────
#  LEGACY shim — kept for any imports still using old name
# ─────────────────────────────────────────────────────────────────────────────
def get_instruments():
    return _get_instruments()

def fetch_option_ltp(security_id: str) -> float:
    return _fetch_option_ltp_raw(security_id)
