from .logger import log
from .config import LOT_SIZE, MAX_TRADE_BUDGET
from .market_data import fetch_ltp

def fetch_options_chain(spot_price: float):
    """
    Mocks fetching the options chain from IndStocks.
    Since options data is highly dynamic, we simulate finding the ATM strike.
    """
    # Round to nearest 50 for Nifty ATM
    atm_strike = round(spot_price / 50) * 50
    return {
        "atm": atm_strike,
        "ce_strikes": [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100],
        "pe_strikes": [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]
    }

def select_strike(direction: str, spot_price: float) -> dict:
    """
    Selects the best strike based on ₹2000 budget constraint.
    Max premium we can afford = 2000 / 25 = ₹80.
    """
    chain = fetch_options_chain(spot_price)
    atm = chain["atm"]
    
    max_premium_allowed = MAX_TRADE_BUDGET / LOT_SIZE
    
    # In reality, we'd fetch the LTP of these strikes from IndStocks.
    # Here, we simulate finding an OTM/ATM strike that fits the budget.
    
    selected_strike = atm
    opt_type = "CE" if "LONG" in direction else "PE"
    
    # Simulate picking a strike slightly OTM to fit the ₹80 budget if ATM is too expensive.
    if opt_type == "CE":
        selected_strike = atm + 50  # OTM CE to make it cheaper
    else:
        selected_strike = atm - 50  # OTM PE to make it cheaper
        
    simulated_premium = min(75.0, max_premium_allowed - 2.0) # Just mock value under budget
    
    security_id = f"NIFTY_OPT_{selected_strike}_{opt_type}"
    
    return {
        "security_id": security_id,
        "strike": selected_strike,
        "type": opt_type,
        "simulated_premium": simulated_premium
    }

def calculate_qty(budget, option_premium, lot_size=LOT_SIZE):
    """
    Calculate max lots based on strict budget.
    """
    if option_premium <= 0: return 0, 0, 0
    
    max_lots = int(budget // (option_premium * lot_size))
    
    if max_lots < 1:
        log.warning(f"⚠️ Budget {budget} too low for premium {option_premium} (needs {option_premium*lot_size})")
        return 0, 0, 0
        
    # Cap to max 1 lot for extremely tight ₹2000 budget to prevent overexposure
    max_lots = min(max_lots, 1) 
    
    qty = max_lots * lot_size
    cost = qty * option_premium
    return qty, cost, max_lots
