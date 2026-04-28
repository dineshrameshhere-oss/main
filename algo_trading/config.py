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
VOLUME_MULT_SCALP = 1.5          # FIX: raised from 1.1 — 10% above avg is noise, 50% is a real surge

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
DEFAULT_SL_PCT      = 0.10              # Initial hard SL: 10% of premium paid
DEFAULT_TP_PCT      = 0.20             # Initial TP (overridden once trailing kicks in)
MAX_DAILY_LOSS_PCT  = 0.15              # Circuit breaker: stop trading if down 15% on the day

# ── Multi-Indicator Rating Thresholds ────────────────────────────────────────
# Based on TradingView Technical Ratings normalised score (-1 to +1 range)
# We use a -3 to +3 range (3 category groups each contributing -1 to +1)
RATING_STRONG_BUY   = 0.7    # score >= +0.7 → STRONG_BUY  (buy CE, full size)
RATING_BUY          = 0.3    # score >= +0.3 → BUY         (buy CE)
RATING_STRONG_SELL  = -0.7   # score <= -0.7 → STRONG_SELL (buy PE, full size)
RATING_SELL         = -0.3   # score <= -0.3 → SELL        (buy PE)
VOLUME_MULT_SURGE   = 2.0    # Volume spike threshold for bonus score
ADX_TREND_MIN       = 20     # ADX below this = choppy, halve all signals
ADX_TREND_STRONG    = 25     # ADX above this = trending, normal signals

# ── Stepped Trailing SL Ladder ────────────────────────────────────────────────
# Revised: starts at +5% (achievable even on lower-delta options)
# Each tuple: (profit_trigger_pct, locked_sl_floor_pct)
# SL only moves UP — never down.
TRAILING_STEPS = [
    (0.05, 0.02),   # +5% profit   → lock in +2%   (breakeven-ish)
    (0.10, 0.05),   # +10% profit  → lock in +5%
    (0.20, 0.10),   # +20% profit  → lock in +10%
    (0.30, 0.15),   # +30% profit  → lock in +15%
    (0.40, 0.20),   # +40% profit  → lock in +20%
    (0.50, 0.25),   # +50% profit  → lock in +25%
    (0.60, 0.30),   # +60% profit  → lock in +30%
    (0.70, 0.35),   # +70% profit  → lock in +35%
    (0.80, 0.40),   # +80% profit  → lock in +40%
    (0.90, 0.45),   # +90% profit  → lock in +45%
    (1.00, 0.50),   # +100% profit → lock in +50%  (doubled money)
    (1.50, 0.75),   # +150% profit → lock in +75%
    (2.00, 1.00),   # +200% profit → lock in +100% (full entry recovered)
]
