from .logger import log
from .config import LOT_SIZE, MAX_TRADE_BUDGET
from .market_data import fetch_ltp

from .market_data import get_auth_headers
from .config import INDSTOCKS_BASE
import requests
import pandas as pd
import io

INSTRUMENTS_DF = None

def get_instruments():
    global INSTRUMENTS_DF
    if INSTRUMENTS_DF is not None:
        return INSTRUMENTS_DF
    try:
        url = f"{INDSTOCKS_BASE}/market/instruments?source=fno"
        res = requests.get(url, headers=get_auth_headers(), timeout=10)
        if res.status_code == 200:
            INSTRUMENTS_DF = pd.read_csv(io.StringIO(res.text))
            return INSTRUMENTS_DF
    except Exception as e:
        log.error(f"❌ Error fetching instruments: {e}")
    return pd.DataFrame()

def get_security_id_for_option(strike: float, opt_type: str):
    """
    Finds the INDMoney SECURITY_ID for a given NIFTY strike and CE/PE type.
    """
    df = get_instruments()
    if df.empty:
        # Fallback to dummy ID if CSV fails (for paper trading without token)
        return f"NFO_DUMMY_{int(strike)}_{opt_type}"
        
    try:
        # Filter for NIFTY options
        nifty_opts = df[(df['SYMBOL_NAME'] == 'NIFTY') & (df['INSTRUMENT_NAME'] == 'OPTIDX') & (df['OPTION_TYPE'] == opt_type)].copy()
        
        # Ensure Expiry is datetime and sort to get nearest
        nifty_opts['EXPIRY_DATE'] = pd.to_datetime(nifty_opts['EXPIRY_DATE'])
        
        # We need future expiries only
        import datetime
        now = pd.to_datetime(datetime.date.today())
        future_opts = nifty_opts[nifty_opts['EXPIRY_DATE'] >= now]
        if future_opts.empty:
            future_opts = nifty_opts # fallback
            
        closest_expiry = future_opts['EXPIRY_DATE'].min()
        
        # Find the specific strike for the closest expiry
        specific_opt = future_opts[(future_opts['EXPIRY_DATE'] == closest_expiry) & (future_opts['STRIKE_PRICE'] == strike)]
        
        if not specific_opt.empty:
            sec_id = specific_opt.iloc[0]['SECURITY_ID']
            return f"NFO_{sec_id}"  # The API requires SEGMENT_INSTRUMENTTOKEN format for quotes
    except Exception as e:
        log.error(f"❌ Error parsing option security ID: {e}")
        
    return f"NFO_DUMMY_{int(strike)}_{opt_type}"

def fetch_options_chain(spot_price: float):
    """
    Calculates the strikes around ATM.
    Since INDMoney doesn't have a direct option chain endpoint, we use the instrument CSV to resolve them.
    """
    # Round to nearest 50 for Nifty ATM
    atm_strike = round(spot_price / 50) * 50
    
    return {
        "atm": atm_strike,
        "ce_strikes": [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100],
        "pe_strikes": [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]
    }

def fetch_option_ltp(security_id: str) -> float:
    """
    Fetches live premium for a specific option strike from INDMoney.
    """
    try:
        url = f"{INDSTOCKS_BASE}/market/quotes/ltp?scrip-codes={security_id}"
        res = requests.get(url, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            data = res.json()
            return float(data.get('data', {}).get(security_id, {}).get('live_price', 0))
    except Exception as e:
        log.error(f"❌ INDMoney fetch_option_ltp error: {e}")
    return 0.0

def select_strike(direction: str, spot_price: float, budget: float) -> dict:
    """
    Selects the best strike based on available budget using real live INDMoney premiums.
    """
    chain = fetch_options_chain(spot_price)
    atm = chain["atm"]
    
    opt_type = "CE" if "LONG" in direction else "PE"
    strikes_to_check = chain["ce_strikes"] if opt_type == "CE" else chain["pe_strikes"]
    
    # Sort strikes by closeness to ATM
    strikes_to_check.sort(key=lambda x: abs(x - atm))
    
    best_strike = atm
    best_premium = 0.0
    
    # Iterate through strikes and query INDMoney for live LTP
    for strike in strikes_to_check:
        security_id = get_security_id_for_option(strike, opt_type)
        live_premium = fetch_option_ltp(security_id)
        
        if live_premium > 0:
            total_cost = live_premium * LOT_SIZE
            if total_cost <= budget:
                # We want the most expensive option we can afford (highest Delta)
                if live_premium > best_premium:
                    best_premium = live_premium
                    best_strike = strike

    if best_premium == 0.0:
        log.warning("⚠️ Could not find a valid option premium within budget from INDMoney API.")
        # Fallback for paper testing without auth token
        if max_premium_allowed := budget / LOT_SIZE:
            best_premium = min(max_premium_allowed, 80.0)
            
    final_security_id = get_security_id_for_option(best_strike, opt_type)
    
    return {
        "security_id": final_security_id,
        "strike": best_strike,
        "type": opt_type,
        "simulated_premium": best_premium
    }

def calculate_dynamic_risk(premium: float):
    """
    Scales TP/SL based on the quality (Delta) of the option.
    OTM (cheap) needs more room. ITM (expensive) hits TP fast.
    """
    if premium < 100.0:
        # High volatility OTM
        return 0.10, 0.20
    elif premium < 180.0:
        # ATM
        return 0.075, 0.15
    else:
        # Stable ITM
        return 0.05, 0.10

def calculate_qty(budget, option_premium, lot_size=LOT_SIZE):
    """
    Calculate max lots based on budget.
    Always exactly 1 lot for this strategy to ensure quality upgrades.
    """
    if option_premium <= 0: return 0, 0, 0
    
    if budget < option_premium * lot_size:
        log.warning(f"⚠️ Budget {budget} too low for premium {option_premium}")
        return 0, 0, 0
        
    qty = 1 * lot_size
    cost = qty * option_premium
    return qty, cost, 1
