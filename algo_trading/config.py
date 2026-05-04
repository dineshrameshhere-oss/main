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
LOT_SIZE          = 65                   # Nifty lot size (confirmed by broker API: must be multiple of 65)

# ── Position Scaling Tiers (Quality Premium Focus) ───────────────────────────
# Each lot = 65 units. We prioritize QUALITY (delta ≥ 0.25) over quantity.
# With ₹5K budget, 1 lot @ ₹70 = ₹4550 — the maximum affordable quality strike.
#
# Capital Tier     | Max Lots | Approx. Cost per Trade
# -----------------|----------|----------------------------------
# ₹5,000           | 1 lot    | ₹2,600 - ₹5,000 (Premium ₹40–₹76)
# ₹15,000          | 2 lots   | ₹5,200 - ₹9,750
# ₹30,000          | 3 lots   | ₹7,800 - ₹14,625
# ₹60,000          | 4 lots   | ₹10,400 - ₹19,500
# ₹1,20,000        | 5 lots   | ₹13,000 - ₹24,375
#
# NOTE: Only scale up on STRONG signals (score >= 0.85).
LOT_SCALE_TIERS = [
    (5_000,   1),   # ₹5K  → 1 lot (65 units)
    (15_000,  2),   # ₹15K → 2 lots
    (30_000,  3),   # ₹30K → 3 lots
    (60_000,  4),   # ₹60K → 4 lots
    (120_000, 5),   # ₹120K → 5 lots
]

# ── Entry Quality Gates ────────────────────────────────────────────────────────
# Hard stops before placing any trade. Better to miss a trade than take a bad one.
MIN_DELTA_ENTRY   = 0.25   # Minimum delta: below this, option barely moves per Nifty point
MIN_PREMIUM_ENTRY = 40.0   # Minimum premium ₹: below this, bid-ask spread eats SL instantly

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

# Risk Defaults
# DEFAULT_SL_PCT is used by risk_manager as initial hard-SL floor ONLY when the
# order dict does not provide a valid sl_price. Normally sl_price from
# calculate_dynamic_risk (wider, premium-calibrated) is used instead.
DEFAULT_SL_PCT      = 0.15             # 15% fallback floor (wide enough to survive spread noise)
DEFAULT_TP_PCT      = 0.60             # 60% initial TP notification (let trailing SL run past it)
MAX_DAILY_LOSS_PCT  = 0.20             # Circuit: stop if down 20% of entry value

# ── Multi-Indicator Rating Thresholds ────────────────────────────────────────
# Raised significantly. Target: 2-3 high-quality trades per week, not 3/day noise.
RATING_STRONG_BUY        = 0.70   # High conviction only — skips marginal/false signals
RATING_BUY               = 0.45
RATING_STRONG_SELL       = -0.70
RATING_SELL              = -0.45
RATING_AFTERNOON_RELAXED = 0.60   # Still requires quality even with 0 trades (was 0.40)
VOLUME_MULT_SURGE        = 2.5    # Volume surge 150%+
ADX_TREND_MIN            = 25     # Skip if trending too weakly (was 22)
ADX_TREND_STRONG         = 32     # Only strongest trends warrant entry (was 28)

# ── Realized Volatility Gate ──────────────────────────────────────────────────
# Skip all entries on flat days — if Nifty hasn't moved enough, premiums won't.
# Measured as High-Low range of last 12 bars (1 hour of 5m candles).
# 0.5% of 24000 = 120 pts. A genuine trending move needs at least this range.
# Flat days (range < 120pts) produce choppy options — nothing moves 10-200%.
MIN_NIFTY_HOURLY_RANGE_PCT = 0.005   # 0.5% of current price (raised from 0.3%)

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
# Let winners run. First rung at +20% gives breathing room against noise.
# Each tuple: (profit_trigger_pct, locked_sl_floor_pct). SL only moves UP.
TRAILING_STEPS = [
    (0.20, 0.08),   # +20% → lock +8%   (first lock — now definitely profitable)
    (0.30, 0.15),   # +30% → lock +15%
    (0.40, 0.22),   # +40% → lock +22%
    (0.50, 0.30),   # +50% → lock +30%  (solid win locked)
    (0.75, 0.45),   # +75% → lock +45%
    (1.00, 0.60),   # +100% → lock +60% (doubled money, can't lose now)
    (1.50, 0.80),   # +150% → lock +80%
    (2.00, 1.00),   # +200% → lock +100% (tripled money)
    (3.00, 1.50),   # +300% → lock +150% (extreme momentum day)
]

# Brokerage + API cost per round-trip (buy + sell)
# INDMoney: ₹20 flat × 2 sides = ₹40  |  API/exchange: ~₹10
BROKERAGE_PER_TRADE = 50.0   # Rs  (used to enforce min floor covers costs)
