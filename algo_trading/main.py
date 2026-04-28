import os
from dotenv import load_dotenv

from .logger import log
from .scheduler import start_scheduler, job_premarket, job_market_open
from .config import MAX_TRADE_BUDGET, DEFAULT_SL_PCT, DEFAULT_TP_PCT

def print_banner():
    print("\033[96m" + "="*50)
    print("🚀 TERMINAL ALGO TRADER - NIFTY 50 SCALPER")
    print("="*50 + "\033[0m")
    print(f"💰 Fixed Budget : ₹{MAX_TRADE_BUDGET}")
    print(f"🛑 Scalp SL     : {DEFAULT_SL_PCT*100}%")
    print(f"🎯 Scalp TP     : {DEFAULT_TP_PCT*100}%")
    print(f"🧠 AI Engine    : Google Gemini 2.0")
    print("="*50)

def main():
    load_dotenv()
    print_banner()
    
    ans = input("▶️ Start in LIVE TRADING mode? (Type YES to confirm): ").strip()
    live_mode = (ans == "YES")
    
    if live_mode:
        log.warning("🔴 DANGER: LIVE TRADING MODE ENGAGED.")
    else:
        log.info("🟢 PAPER TRADING MODE ACTIVE.")
        
    test = input("▶️ Run a quick mock cycle right now? (y/n): ").strip().lower()
    if test == 'y':
        log.info("🏃 Running mock cycle...")
        job_premarket()
        job_market_open()
        
    start_scheduler(live_mode)

if __name__ == "__main__":
    main()
