import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
INDSTOCKS_BASE    = "https://api.indstocks.com"
NIFTY_SCRIP_CODE  = "NSE_3045"           # Nifty 50 index scrip code
FINNIFTY_SCRIP_CODE = "NSE_2885"         # FinNifty (Nifty Financial Services) — used as confirmation signal
NIFTY_SEGMENT     = "DERIVATIVE"
NIFTY_EXCHANGE    = "NSE"
PRODUCT_TYPE      = "MARGIN"
ALGO_ID           = "99999"

# ── Scalping Bot Safety Net ───────────────────────────────────────────────────
# If 0 trades by 12:30 IST, relax STRONG_BUY from 0.45 → 0.40 for afternoon.
RATING_AFTERNOON_RELAXED = 0.40    
AFTERNOON_HOUR           = 12      
AFTERNOON_MIN            = 30      

# ── Intraday Deep Learning Bot Configuration ──────────────────────────────────
INTRADAY_POLL_INTERVAL_MIN = 15          # Intraday runs on 15m candles
INTRADAY_MAX_HOLD_MIN      = 120         # Hold for up to 2 hours (8 bars × 15m)
INTRADAY_TP_PCT            = 40.0        # 40% target for multi-hour hold
INTRADAY_SL_PCT            = -15.0       # 15% stop loss for wider swings
INTRADAY_TSL_ACTIVATION    = 15.0        # Activate TSL at 15% profit
INTRADAY_TSL_TRAIL         = 8.0         # Trail by 8%

# Trading Constants
LOT_SIZE          = 75                   # Nifty options lot size (updated to 75 as per NSE 2026)

# ── Position Scaling Tiers (Quality Premium Focus) ───────────────────────────
# Each lot = 75 units. We prioritize QUALITY (ATM/Shallow OTM) over quantity.
# High Delta (0.45+) premiums cost more (₹100–₹250), so we scale slower.
#
# Capital Tier     | Max Lots | Approx. Cost per Trade
# -----------------|----------|----------------------------------
# ₹5,000           | 1 lot    | ₹2,500 - ₹5,000 (Cheap OTM needed if premium > 66)
# ₹15,000          | 2 lots   | ₹7,500 - ₹15,000
# ₹30,000          | 3 lots   | ₹12,500 - ₹22,000
# ₹60,000          | 4 lots   | ₹18,000 - ₹30,000
# ₹1,20,000        | 5 lots   | ₹25,000 - ₹45,000
#
# NOTE: Only scale up on STRONG_BUY signals (score >= 0.85).
LOT_SCALE_TIERS = [
    (5_000,   1),   # ₹5K  → 1 lot (75 units)
    (15_000,  2),   # ₹15K → 2 lots
    (30_000,  3),   # ₹30K → 3 lots
    (60_000,  4),   # ₹60K → 4 lots
    (120_000, 5),   # ₹120K → 5 lots
]

# LLM Config
GEMINI_MODEL      = "gemini-2.0-flash"

# Scheduler times (IST 24h)
TIME_PRE_MARKET   = "09:00"             # Step 1: historical context to LLM
TIME_MARKET_OPEN  = "09:30"             # Step 3: first 30-min candle analysis
TIME_EOD_CHECK    = "15:15"             # Close all open positions before expiry

# ── SCALPING indicators (5-min chart) OTM MOMENTUM STRATEGY
EMA_FAST          = 3           # Faster EMA for quick momentum detection
EMA_SLOW          = 8           # Medium EMA for trend confirmation
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

# Risk Defaults (OTM HIGH-PROFIT Scalping)
DEFAULT_SL_PCT      = 0.05              # Tight SL: 5% (cut losses fast on OTM)
DEFAULT_TP_PCT      = 0.25             # 25% TP (realistic for OTM, +₹25-30 per ₹100 premium)
MAX_DAILY_LOSS_PCT  = 0.20              # Circuit: stop if down 20% (loose for catching big moves)

# ── Multi-Indicator Rating Thresholds (OTM STRICT) ────────────────────────────────────────
RATING_STRONG_BUY   = 0.55   # Raised: STRONG_BUY only (skip marginal signals)
RATING_BUY          = 0.40   # Raised: fewer entries, higher quality
RATING_STRONG_SELL  = -0.55  # Raised: STRONG_SELL only
RATING_SELL         = -0.40  # Raised: higher threshold
VOLUME_MULT_SURGE   = 2.5    # Raised: Volume surge 150%+ (high conviction)
ADX_TREND_MIN       = 22     # Raised: Skip if too choppy
ADX_TREND_STRONG    = 28     # Raised: Only strongest trends

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

# ── Stepped Trailing SL Ladder (OTM SCALP) ────────────────────────────────────────────────
# Aggressive step-ups for momentum spikes (faster ratcheting than ATM)
# Each tuple: (profit_trigger_pct, locked_sl_floor_pct)
# SL only moves UP — never down.
TRAILING_STEPS = [
    (0.10, 0.05),   # +10% profit → lock in +5%   (first momentum rung)
    (0.15, 0.08),   # +15% profit → lock in +8%
    (0.20, 0.12),   # +20% profit → lock in +12%  (PRIMARY TP ZONE — likely hit)
    (0.25, 0.15),   # +25% profit → lock in +15%  (TARGET HIT — secure win)
    (0.30, 0.18),   # +30% profit → lock in +18%
    (0.40, 0.25),   # +40% profit → lock in +25%
    (0.50, 0.32),   # +50% profit → lock in +32%
    (0.75, 0.45),   # +75% profit → lock in +45%
    (1.00, 0.60),   # +100% profit → lock in +60%
    (1.50, 0.80),   # +150% profit → lock in +80%
    (2.00, 1.00),   # +200% profit → lock in +100%
]

# Brokerage + API cost per round-trip (buy + sell)
# INDMoney: ₹20 flat × 2 sides = ₹40  |  API/exchange: ~₹10
BROKERAGE_PER_TRADE = 50.0   # Rs  (used to enforce min floor covers costs)
