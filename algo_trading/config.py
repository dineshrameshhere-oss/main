import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
INDSTOCKS_BASE    = "https://api.indstocks.com"
NIFTY_SCRIP_CODE  = "NSE_3045"           # Nifty 50 index scrip code
NIFTY_SEGMENT     = "DERIVATIVE"
NIFTY_EXCHANGE    = "NSE"
PRODUCT_TYPE      = "MARGIN"
ALGO_ID           = "99999"

# Trading Constants
LOT_SIZE          = 25                   # Nifty options lot size (updated to 25)
MAX_TRADE_BUDGET  = 2000                 # Strict low capital focus

# LLM Config
GEMINI_MODEL      = "gemini-2.0-flash"

# Scheduler times (IST 24h)
TIME_PRE_MARKET   = "09:00"             # Step 1: historical context to LLM
TIME_MARKET_OPEN  = "09:30"             # Step 3: first 30-min candle analysis
TIME_EOD_CHECK    = "15:15"             # Close all open positions before expiry

# ── SCALPING indicators (1-min / 3-min chart)
EMA_FAST          = 5           # Changed from 9 to 5 for faster 5m entries
EMA_SLOW          = 13          # Changed from 21 to 13 for faster 5m entries
RSI_PERIOD        = 14
RSI_OVERBOUGHT    = 75          # Relaxed slightly to not miss strong trends
RSI_OVERSOLD      = 25          # Relaxed slightly to not miss strong trends
SUPERTREND_PERIOD = 10
SUPERTREND_MULT   = 3.0
VWAP_ENABLED      = True
VOLUME_MULT_SCALP = 1.1

# ── INTRADAY / SWING constants (Kept for completeness but bot focuses on SCALP)
INTRADAY_EMA_FAST    = 20
INTRADAY_EMA_SLOW    = 50
MACD_FAST            = 12
MACD_SLOW            = 26
MACD_SIGNAL          = 9
ADX_PERIOD           = 14
ADX_TREND_THRESHOLD  = 25
BB_PERIOD            = 20
BB_STD               = 2.0
CPR_ENABLED          = True
VOLUME_MULT_INTRADAY = 1.15

SWING_EMA_MID        = 50
SWING_EMA_LONG       = 200
ICHIMOKU_ENABLED     = True
SWING_ADX_MIN        = 20
SWING_RSI_BULL_MIN   = 50
SWING_RSI_BEAR_MAX   = 50

# Risk Defaults (Aggressive for Scalping)
DEFAULT_SL_PCT    = 0.10                # 10% SL on premium paid (fast cut)
DEFAULT_TP_PCT    = 0.20                # 20% TP on premium paid (fast grab)
TRAILING_SL_PCT   = 0.10                # Trail by 10% from peak
