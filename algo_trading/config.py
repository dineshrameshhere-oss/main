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
DEFAULT_TP_PCT      = 0.08             # Initial TP: 8% (achievable — avg peak is +1.6% per trade)
MAX_DAILY_LOSS_PCT  = 0.15              # Circuit breaker: stop trading if down 15% on the day

# ── Multi-Indicator Rating Thresholds ────────────────────────────────────────
RATING_STRONG_BUY   = 0.45   # score >= +0.45 → STRONG_BUY (calibrated to normalised leading score)
RATING_BUY          = 0.2    # score >= +0.2 → BUY
RATING_STRONG_SELL  = -0.45  # score <= -0.45 → STRONG_SELL
RATING_SELL         = -0.2   # score <= -0.2 → SELL
VOLUME_MULT_SURGE   = 2.0    # Volume spike threshold for bonus score
ADX_TREND_MIN       = 20     # ADX below this = choppy, halve all signals
ADX_TREND_STRONG    = 25     # ADX above this = trending, normal signals

# ── Realized Volatility Gate ──────────────────────────────────────────────────
# Skip all entries on flat days — if Nifty hasn't moved enough, premiums won't.
# Measured as High-Low range of last 12 bars (1 hour of 5m candles).
# Using % of current price so it works regardless of data scale (1107 or 24000).
# 0.3% of 24000 = 72 pts. 0.3% of 1107 = 3.3 pts. Both meaningful.
MIN_NIFTY_HOURLY_RANGE_PCT = 0.003   # 0.3% of current price

# ── PCR (Put-Call Ratio) Directional Gate ─────────────────────────────────────
# Applied only after 11:00 AM (OI not meaningful before that).
# Smart money positioning: PCR extreme = don't fight the flow.
PCR_BULLISH_MAX = 0.70   # PCR below this = strong bullish OI → block PE entries
PCR_BEARISH_MIN = 1.30   # PCR above this = strong bearish OI → block CE entries
PCR_APPLY_AFTER_HOUR = 11  # IST hour after which PCR is reliable

# ── Candle Quality (False Breakout Filter) ────────────────────────────────────
# A doji or rejection candle at signal bar = probable fake breakout → skip trade.
# Body ratio = candle body / total range. Low body ratio = indecision/rejection.
CANDLE_MIN_BODY_RATIO = 0.20   # body must be ≥20% of candle range (20% = only blocks genuine dojis)
CANDLE_REJECTION_WICK = 2.0    # wick-to-body ratio above which = rejection candle

# ── Short-term Momentum Filter ────────────────────────────────────────────────
# Require directional momentum BEFORE entry: price must be trending in signal dir.
# Checked as: % net price move over last N bars.
# 0.05% of 24000 = 12 pts. 0.05% of 1107 = 0.55 pts.
# Works correctly regardless of underlying price scale.
MOMENTUM_BARS       = 6     # look-back bars (6 × 5min = 30 min)
MOMENTUM_MIN_MOVE_PCT = 0.0005  # 0.05% net move in signal direction over 30 min

# ── Stepped Trailing SL Ladder ────────────────────────────────────────────────
# Calibrated to actual option move distribution (avg peak +1.6%, max ~15%).
# Starts locking gains at just +3% so even small moves are captured.
# Each tuple: (profit_trigger_pct, locked_sl_floor_pct)
# SL only moves UP — never down.
TRAILING_STEPS = [
    (0.03, 0.015),  # +3%  profit → lock in +1.5%  (covers ₹50 brokerage on ₹2000 trade)
    (0.05, 0.025),  # +5%  profit → lock in +2.5%
    (0.08, 0.04),   # +8%  profit → lock in +4%    (TP hit zone — guarantee profit)
    (0.10, 0.06),   # +10% profit → lock in +6%
    (0.15, 0.08),   # +15% profit → lock in +8%
    (0.20, 0.12),   # +20% profit → lock in +12%
    (0.30, 0.18),   # +30% profit → lock in +18%
    (0.40, 0.24),   # +40% profit → lock in +24%
    (0.50, 0.30),   # +50% profit → lock in +30%
    (0.75, 0.45),   # +75% profit → lock in +45%
    (1.00, 0.60),   # +100% profit → lock in +60%
    (1.50, 0.80),   # +150% profit → lock in +80%
    (2.00, 1.00),   # +200% profit → lock in +100% (full entry recovered)
]

# Brokerage + API cost per round-trip (buy + sell)
# INDMoney: ₹20 flat × 2 sides = ₹40  |  API/exchange: ~₹10
BROKERAGE_PER_TRADE = 50.0   # Rs  (used to enforce min floor covers costs)
