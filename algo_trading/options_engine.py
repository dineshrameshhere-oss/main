from .logger import log
from . import config
from .config import INDSTOCKS_BASE, PCR_BULLISH_MAX, PCR_BEARISH_MIN, MIN_DELTA_ENTRY, MIN_PREMIUM_ENTRY
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
            # INDMoney typically uses 'LOT_SIZE', 'LOT_UNITS' or 'MIN_LOT_QUANTITY'
            lot_col = next((c for c in ['LOT_UNITS', 'LOT_SIZE', 'MIN_LOT_QUANTITY', 'FREEZE_QTY'] if c in _INSTRUMENTS_DF.columns), None)
            
            # Filter for NIFTY OPTIDX first to ensure we are looking at the right instrument
            nifty_mask = (
                _INSTRUMENTS_DF['TRADING_SYMBOL'].str.upper().str.contains('NIFTY', na=False) &
                (_INSTRUMENTS_DF['INSTRUMENT_NAME'] == 'OPTIDX')
            )
            nifty_sample = _INSTRUMENTS_DF[nifty_mask]

            if not nifty_sample.empty:
                # Priority: Look for a row that specifically mentions '65' or 'LOT_SIZE'
                # Sometimes the CSV has multiple Nifty rows, we want the most recent/active one
                active_row = nifty_sample.iloc[0]
                
                # If we found a lot column, use it
                if lot_col:
                    raw_lot = int(active_row[lot_col])
                    # Sanity check: Nifty lot size must be 50-100 (NSE standard range).
                    # CSV sometimes returns FREEZE_QTY (1300+) or wrong column.
                    if 50 <= raw_lot <= 100:
                        config.LOT_SIZE = raw_lot
                        log.info(f"📊 DYNAMIC LOT SIZE DETECTED: {config.LOT_SIZE} (from {lot_col})")
                    else:
                        log.warning(f"⚠️ Lot size from CSV ({raw_lot}) out of range [50-100] — using default {config.LOT_SIZE}")
                else:
                    log.warning("⚠️ Dynamic Lot Size: Column not found, using default.")
            else:
                log.warning("⚠️ Dynamic Lot Size: Could not find NIFTY OPTIDX in instruments.")
                config.LOT_SIZE = 65 # Safe fallback for Nifty

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
        # ── Hard quality gates ─────────────────────────────────────────────
        if best_choice['delta'] < MIN_DELTA_ENTRY:
            log.warning(
                f"🚫 Best affordable strike {best_choice['strike']} {opt_type} "
                f"has delta {best_choice['delta']:.2f} < {MIN_DELTA_ENTRY} minimum. "
                f"Skipping trade — budget too tight for quality strike."
            )
            return {"security_id": f"NFO_DUMMY_{int(atm)}_{opt_type}", "strike": atm,
                    "type": opt_type, "days_to_expiry": 7, "simulated_premium": 0.0}

        if best_choice['premium'] < MIN_PREMIUM_ENTRY:
            log.warning(
                f"🚫 Best strike {best_choice['strike']} {opt_type} "
                f"premium ₹{best_choice['premium']:.2f} < ₹{MIN_PREMIUM_ENTRY} minimum. "
                f"Bid-ask spread would eat SL — skipping."
            )
            return {"security_id": f"NFO_DUMMY_{int(atm)}_{opt_type}", "strike": atm,
                    "type": opt_type, "days_to_expiry": 7, "simulated_premium": 0.0}

        log.info(f"✅ Quality Match: NIFTY {best_choice['strike']} {opt_type} @ Rs {best_choice['premium']:.2f} (Delta {best_choice['delta']:.2f})")
        return {
            "security_id":       best_choice['sec_id'],
            "strike":            best_choice['strike'],
            "type":              opt_type,
            "days_to_expiry":    best_choice['days_to_exp'],
            "simulated_premium": best_choice['premium']
        }
    else:
        log.warning("⚠️ No affordable strikes found.")
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
    SL/TP calibrated to the actual option premium level.

    The TP is an initial notification trigger only — the trailing SL ladder
    in config.py handles the actual exit and lets winners run to 100-200%+.
    The SL here is the REAL hard floor used by risk_manager (not DEFAULT_SL_PCT).

    Wider SL for cheaper options because:
    - Bid-ask spread on ₹50 option can be ₹1-3 (2-6% alone)
    - A 1% Nifty noise move hits ₹50 option hard % wise but barely in ₹
    - We need room to survive the first 5-10 minutes of trade
    """
    if premium < 60:
        return 0.20, 0.80   # Shallow OTM: 20% SL, 80% TP trigger (bid-ask wide)
    elif premium < 120:
        return 0.15, 0.60   # Near OTM: 15% SL, 60% TP trigger
    elif premium < 200:
        return 0.10, 0.40   # ATM-ish: 10% SL, 40% TP trigger
    else:
        return 0.07, 0.30   # ITM: tight SL, 30% TP trigger


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
#  SENSEX BSE OPTIONS  (fallback when Nifty delta-gate fires)
#  Sensex lot size = 10 → ATM ~₹300 × 10 = ₹3K, fits small budgets.
#  Uses same directional signal as Nifty (0.97 correlation).
# ─────────────────────────────────────────────────────────────────────────────
_SENSEX_INSTRUMENTS_DF = None


def _get_sensex_instruments() -> pd.DataFrame:
    """Load BSE FNO instruments for Sensex options. Cached for session."""
    global _SENSEX_INSTRUMENTS_DF
    if _SENSEX_INSTRUMENTS_DF is not None:
        return _SENSEX_INSTRUMENTS_DF
    try:
        url = f"{INDSTOCKS_BASE}/market/instruments?source=bse_fno"
        res = requests.get(url, headers=get_auth_headers(), timeout=10)
        if res.status_code == 200:
            df = pd.read_csv(io.StringIO(res.text))
            df.columns = df.columns.str.strip().str.upper()
            df['EXPIRY_DATE'] = pd.to_datetime(df['EXPIRY_DATE'], errors='coerce')
            _SENSEX_INSTRUMENTS_DF = df
            log.info(f"[SENSEX] BSE instruments loaded: {len(df)} rows")
            return df
        log.warning(f"[SENSEX] BSE instruments API {res.status_code}: {res.text[:80]}")
    except Exception as e:
        log.warning(f"[SENSEX] Instruments load error: {e}")
    _SENSEX_INSTRUMENTS_DF = pd.DataFrame()
    return pd.DataFrame()


def fetch_sensex_spot() -> float:
    """
    Returns current Sensex level.
    Primary: BSE scrip LTP. Fallback: infer from Nifty spot × ~3.38 ratio.
    """
    try:
        from .config import SENSEX_SCRIP_CODE
        url = f"{INDSTOCKS_BASE}/market/quotes/ltp?scrip-codes={SENSEX_SCRIP_CODE}"
        res = requests.get(url, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            ltp = float(res.json().get('data', {}).get(SENSEX_SCRIP_CODE, {}).get('live_price', 0))
            if ltp > 50000:
                return ltp
    except Exception as e:
        log.warning(f"[SENSEX] Spot fetch error: {e}")
    # Fallback: Sensex ≈ Nifty × 3.38 (approximate ratio, updated periodically)
    nifty = fetch_nifty_spot()
    return round(nifty * 3.38 / 100) * 100


def _fetch_bse_option_ltp(scrip_code: str) -> float:
    """Fetches live BSE option LTP. scrip_code format: 'BFO_<SECURITY_ID>'"""
    try:
        url = f"{INDSTOCKS_BASE}/market/quotes/ltp?scrip-codes={scrip_code}"
        res = requests.get(url, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            return float(res.json().get('data', {}).get(scrip_code, {}).get('live_price', 0))
    except Exception as e:
        log.warning(f"[SENSEX] LTP fetch error for {scrip_code}: {e}")
    return 0.0


def select_sensex_strike(direction: str, budget: float) -> dict:
    """
    Finds a quality Sensex BSE option strike using the same logic as select_strike.
    Sensex lot size = 10 units — very affordable ATM options for small budgets.
    Returns dict with security_id (BFO_ prefix), strike, simulated_premium, exchange='BSE'.
    Returns simulated_premium=0.0 if no quality strike found.
    """
    from .indicators import compute_greeks
    from .config import SENSEX_LOT_SIZE, MIN_DELTA_ENTRY, MIN_PREMIUM_ENTRY

    opt_type = "CE" if "LONG" in direction else "PE"
    spot     = fetch_sensex_spot()
    atm      = round(spot / 100) * 100   # Sensex strikes at 100-pt intervals

    _EMPTY = {"security_id": "", "strike": atm, "type": opt_type,
               "days_to_expiry": 7, "simulated_premium": 0.0, "exchange": "BSE"}

    strikes = [atm + (i * 100) for i in range(-5, 6)]
    log.info(f"[SENSEX] Searching {opt_type} strikes around ATM {atm} | Budget ₹{budget:.0f} | Lot {SENSEX_LOT_SIZE}")

    df = _get_sensex_instruments()
    if df.empty:
        log.warning("[SENSEX] No BSE instruments — Sensex fallback unavailable.")
        return _EMPTY

    today = pd.to_datetime(datetime.date.today())
    sensex_opts = df[
        df['TRADING_SYMBOL'].str.upper().str.contains('SENSEX', na=False) &
        df['INSTRUMENT_NAME'].isin(['OPTIDX', 'IO']) &
        (df['OPTION_TYPE'] == opt_type) &
        (df['EXPIRY_DATE'] >= today)
    ].copy()

    if sensex_opts.empty:
        log.warning("[SENSEX] No Sensex options in BSE instruments.")
        return _EMPTY

    near_expiry  = sensex_opts['EXPIRY_DATE'].min()
    near_opts    = sensex_opts[sensex_opts['EXPIRY_DATE'] == near_expiry]
    days_to_exp  = max(1, (near_expiry.date() - datetime.date.today()).days)

    candidates = []
    for strike in strikes:
        row = near_opts[near_opts['STRIKE_PRICE'] == float(strike)]
        if row.empty:
            continue
        sec_id = f"BFO_{int(row.iloc[0]['SECURITY_ID'])}"
        ltp    = _fetch_bse_option_ltp(sec_id)
        if ltp <= 0:
            continue
        greeks = compute_greeks(spot, strike, days_to_exp, ltp, opt_type)
        cost   = ltp * SENSEX_LOT_SIZE
        log.info(f"  [SENSEX] {strike} {opt_type}: ₹{ltp:.2f} | δ={greeks['delta']:.2f} | Cost ₹{cost:.0f}")
        if cost <= budget:
            candidates.append({'strike': strike, 'premium': ltp, 'sec_id': sec_id,
                                'delta': greeks['delta'], 'days_to_exp': days_to_exp})

    best_choice = None
    quality = [c for c in candidates if 0.40 <= c['delta'] <= 0.65]
    if quality:
        best_choice = max(quality, key=lambda x: x['delta'])
    elif candidates:
        best_choice = max(candidates, key=lambda x: x['delta'])

    if not best_choice:
        log.warning("[SENSEX] No affordable strikes found.")
        return _EMPTY

    if best_choice['delta'] < MIN_DELTA_ENTRY:
        log.warning(f"[SENSEX] Best delta {best_choice['delta']:.2f} < {MIN_DELTA_ENTRY} — skipping.")
        return _EMPTY

    if best_choice['premium'] < MIN_PREMIUM_ENTRY:
        log.warning(f"[SENSEX] Premium ₹{best_choice['premium']:.2f} < ₹{MIN_PREMIUM_ENTRY} — skipping.")
        return _EMPTY

    log.info(f"✅ [SENSEX] Quality Match: SENSEX {best_choice['strike']} {opt_type} "
             f"@ ₹{best_choice['premium']:.2f} (δ={best_choice['delta']:.2f})")
    return {
        "security_id":       best_choice['sec_id'],
        "strike":            best_choice['strike'],
        "type":              opt_type,
        "days_to_expiry":    best_choice['days_to_exp'],
        "simulated_premium": best_choice['premium'],
        "exchange":          "BSE",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  LEGACY shim — kept for any imports still using old name
# ─────────────────────────────────────────────────────────────────────────────
def get_instruments():
    return _get_instruments()

def fetch_option_ltp(security_id: str) -> float:
    return _fetch_option_ltp_raw(security_id)
