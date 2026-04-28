# LLM UNDERSTANDABLE IMPLEMENTATION PLAN
## Python Algo Trading System — Nifty 50 Options
### Target Runtime: Android Termux | Broker: IndStocks API | AI: Google GenAI

---

## ═══ SYSTEM OVERVIEW ═══

A fully automated, LLM-guided options trading bot for Nifty 50 that:
1. Collects and compresses multi-timeframe market data
2. Feeds it to Google Gemini for directional bias (Long/Short/Scalp)
3. Fetches live options chain (ATM/ITM/OTM) and sizes position by user budget
4. Executes trades via IndStocks API
5. Actively monitors with TA indicators for scalping or swing
6. Manages SL/TP automatically

---

## ═══ ENVIRONMENT & CONSTRAINTS ═══

```
Runtime       : Android Termux (arm64-v8a)
Python        : 3.11+ (install via pkg install python)
No GUI        : Terminal only, no matplotlib pop-ups (use matplotlib Agg backend)
Low RAM       : Avoid large in-memory DataFrames; stream/process in chunks
Packages      : pip install requests pandas numpy ta-lib-python schedule 
                google-generativeai python-dotenv
API Keys      : Stored in .env file in project root
Timezone      : Asia/Kolkata (IST), all times in IST
Market Hours  : 09:15 open, 15:30 close, pre-open 09:00–09:15
```

---

## ═══ FILE STRUCTURE ═══

```
algo_trading/
├── main.py                  # Entry point; takes user inputs; starts scheduler
├── config.py                # Constants, API endpoints, env loader
├── market_data.py           # IndStocks market data fetch functions
├── news_fetcher.py          # News/rumor scraper for Nifty 50
├── llm_analyst.py           # Google GenAI prompts and response parsers
├── options_engine.py        # Options chain fetch, ATM/ITM/OTM selector, sizing
├── trade_executor.py        # Order placement via IndStocks smart order API
├── indicators.py            # TA indicators: SuperTrend, VWAP, EMA, RSI, etc.
├── risk_manager.py          # SL/TP logic, trailing stop, position monitor
├── scheduler.py             # APScheduler or schedule-based job runner
├── logger.py                # Colored terminal logger (no file I/O overhead)
└── .env                     # INDSTOCKS_TOKEN, GEMINI_API_KEY
```

---

## ═══ CONFIG.PY ═══

```python
# All constants live here — never hardcode elsewhere

INDSTOCKS_BASE    = "https://api.indstocks.com"
NIFTY_SCRIP_CODE  = "NSE_3045"           # Nifty 50 index scrip code
NIFTY_SEGMENT     = "DERIVATIVE"
NIFTY_EXCHANGE    = "NSE"
PRODUCT_TYPE      = "MARGIN"
ALGO_ID           = "99999"
LOT_SIZE          = 75                   # Nifty options lot size

GEMINI_MODEL      = "gemini-2.0-flash"   # Or gemini-1.5-pro for deeper analysis

# Scheduler times (IST 24h)
TIME_PRE_MARKET   = "09:00"             # Step 1: historical context to LLM
TIME_MARKET_OPEN  = "09:30"             # Step 3: first 30-min candle analysis
TIME_EOD_CHECK    = "15:15"             # Close all open positions before expiry

# ── SCALPING indicators (1-min / 3-min chart)
EMA_FAST          = 9
EMA_SLOW          = 21
RSI_PERIOD        = 14
RSI_OVERBOUGHT    = 72          # Slightly relaxed — avoid missing strong trends
RSI_OVERSOLD      = 28
SUPERTREND_PERIOD = 10
SUPERTREND_MULT   = 3.0
VWAP_ENABLED      = True
VOLUME_MULT_SCALP = 1.1         # 10% above avg is sufficient for scalp signal

# ── INTRADAY indicators (15-min chart)
INTRADAY_EMA_FAST    = 20
INTRADAY_EMA_SLOW    = 50
MACD_FAST            = 12
MACD_SLOW            = 26
MACD_SIGNAL          = 9
ADX_PERIOD           = 14
ADX_TREND_THRESHOLD  = 25       # ADX > 25 = trending; < 20 = avoid
BB_PERIOD            = 20
BB_STD               = 2.0
CPR_ENABLED          = True     # Central Pivot Range — very popular in Indian mkts
VOLUME_MULT_INTRADAY = 1.15

# ── SWING indicators (daily / weekly chart)
SWING_EMA_MID        = 50
SWING_EMA_LONG       = 200
ICHIMOKU_ENABLED     = True     # Conversion=9, Base=26, Span B=52
SWING_ADX_MIN        = 20       # Looser for swing — trends develop slowly
SWING_RSI_BULL_MIN   = 50       # RSI above 50 = bullish swing zone
SWING_RSI_BEAR_MAX   = 50

# Risk defaults (overridable by user input)
DEFAULT_SL_PCT    = 0.30                # 30% SL on premium paid
DEFAULT_TP_PCT    = 0.60                # 60% TP on premium paid
TRAILING_SL_PCT   = 0.20               # Trail by 20% from peak
MAX_TRADE_BUDGET  = None               # Set at runtime from user input
```

---

## ═══ STEP 1 — PRE-MARKET DATA COLLECTION (9:00 AM) ═══

### 1A. Historical OHLCV Data Fetch

**Function:** `market_data.fetch_historical_ohlcv(timeframes=['1mo','1wk','1h'])`

- Source: IndStocks `/market/quotes/full` endpoint with scrip `NSE_3045`
- If IndStocks doesn't provide candlestick history → fall back to `yfinance` for `^NSEI`
- Fetch three timeframes:
  - **Monthly**: last 6 months of monthly candles
  - **Weekly**: last 12 weeks of weekly candles
  - **Hourly**: last 5 days of hourly candles

### 1B. Data Compression for LLM

**Goal:** Minimize tokens. No raw DataFrames to LLM.

**Compression format per candle:**
```
DATE|O|H|L|C|V|CHG%
```
Example:
```
2025-04-18|22150|22380|22050|22290|1.2M|+0.63%
```

**Function:** `market_data.compress_ohlcv_to_string(df) -> str`

- Round prices to nearest integer
- Volume in K/M notation
- Percent change vs prev close
- Prepend summary line: `NIFTY50 | TIMEFRAME | FROM | TO | N_CANDLES`

### 1C. Key Levels Calculation (attach to compressed data)

**Function:** `indicators.compute_key_levels(df) -> dict`

Compute and include:
- **Support/Resistance**: Last 3 major swing highs/lows (pivot points method)
- **52-week high/low**
- **Current trend**: EMA(50) vs EMA(200) state (bull/bear regime)
- **CPR levels**: Daily TC, BC, Pivot, R1, R2, S1, S2 (from previous day OHLC)
- **Weekly Pivot levels**: Weekly R1, R2, S1, S2
- **Ichimoku snapshot**: Cloud color (green/red), price vs cloud position
- **Weekly RSI value**
- **Daily MACD signal** (bullish/bearish, histogram direction)

Format output as compact JSON string appended to compressed OHLCV.

---

## ═══ STEP 2 — NEWS & RUMOR FETCH (9:00 AM, parallel with Step 1) ═══

**Function:** `news_fetcher.fetch_nifty_news() -> str`

Sources to scrape (no auth needed, use `requests` + `BeautifulSoup`):
1. **Moneycontrol RSS**: `https://www.moneycontrol.com/rss/marketreports.xml`
2. **Economic Times Markets**: `https://economictimes.indiatimes.com/markets/rss.cms`
3. **NSE Official Circulars**: `https://www.nseindia.com/api/latest-circular`
4. **Google News search**: query = `"Nifty 50" OR "NSE" news today` via `feedparser`

**Processing:**
- Filter articles from last 24 hours only
- Extract: `[TIME] HEADLINE — SOURCE`
- Deduplicate by headline similarity (simple word overlap check)
- Cap at 20 headlines maximum
- Format as numbered list string

**LLM Sentiment Pre-pass:**
- NOT sent to LLM yet; bundled with Step 1 data for joint analysis prompt

---

## ═══ STEP 3 — LLM ANALYSIS PROMPT #1 (9:00–9:30 AM) ═══

**Function:** `llm_analyst.analyze_premarket(historical_data_str, news_str) -> dict`

### Prompt Template:
```
You are a professional Nifty 50 options trader with 15 years experience.
Analyze the following data and provide a structured trading outlook.

=== HISTORICAL MARKET DATA ===
{compressed_monthly_data}
{compressed_weekly_data}
{compressed_hourly_data}

=== KEY TECHNICAL LEVELS ===
{key_levels_json}

=== TODAY'S NEWS & MARKET SENTIMENT ===
{news_headlines}

Respond ONLY in this exact JSON structure:
{
  "monthly_trend": "BULLISH|BEARISH|SIDEWAYS",
  "weekly_trend": "BULLISH|BEARISH|SIDEWAYS",
  "hourly_trend": "BULLISH|BEARISH|SIDEWAYS",
  "overall_bias": "LONG|SHORT|NEUTRAL",
  "key_support": [level1, level2],
  "key_resistance": [level1, level2],
  "news_sentiment": "POSITIVE|NEGATIVE|NEUTRAL",
  "news_impact": "HIGH|MEDIUM|LOW",
  "expected_range_low": number,
  "expected_range_high": number,
  "strategy_suggestion": "SWING_LONG|SWING_SHORT|SCALP_LONG|SCALP_SHORT|WAIT",
  "confidence": "HIGH|MEDIUM|LOW",
  "reasoning": "2-3 sentence summary"
}
```

**Parse JSON response strictly. On parse failure: retry once with `repair_json()`.**

---

## ═══ STEP 4 — 30-MIN OPEN DATA ANALYSIS (9:30 AM) ═══

**Function:** `market_data.fetch_first_30min_candle() -> dict`

- At 9:30 IST sharp, fetch live quote via `/market/quotes/full?scrip-codes=NSE_3045`
- Compare with 9:00 AM price to compute:
  - Open price, current price, % move in 30 min
  - High/Low of the session so far
  - Volume vs average
  - Gap-up or gap-down amount

**Function:** `llm_analyst.analyze_market_open(premarket_analysis, open_data) -> dict`

### Prompt Template:
```
Pre-market analysis result: {premarket_analysis_json}

First 30 minutes of market data:
Open: {open_price} | Current: {current_price} | High: {high} | Low: {low}
Move: {pct_change}% | Volume: {volume} (avg: {avg_volume})
Gap: {gap_type} {gap_pct}%

Based on all the above, confirm or revise the trading decision.
Respond ONLY in JSON:
{
  "final_direction": "LONG|SHORT|SCALP_LONG|SCALP_SHORT|NO_TRADE",
  "trade_type": "SWING|SCALP|INTRADAY",
  "entry_zone": [lower_price, upper_price],
  "target_1": number,
  "target_2": number,
  "stop_loss": number,
  "confidence": "HIGH|MEDIUM|LOW",
  "reasoning": "1-2 sentences"
}
```

---

## ═══ STEP 5 — OPTIONS SELECTION & POSITION SIZING ═══

### 5A. Fetch Options Chain

**Function:** `options_engine.fetch_options_chain(expiry='current_week') -> dict`

- Hit IndStocks `/market/quotes/full` for current Nifty options
- Scrip codes for options follow NSE format; iterate strike prices
- Filter for **current week's expiry** (nearest Thursday)
- Separate CE (Call) and PE (Put) sides

### 5B. ATM / ITM / OTM Identification

```
ATM = Strike closest to current Nifty spot price (round to nearest 50)
ITM_CE = ATM - 100 (in-the-money call)
OTM_CE = ATM + 100 (out-of-the-money call)
ITM_PE = ATM + 100 (in-the-money put)
OTM_PE = ATM - 100 (out-of-the-money put)
```

**Function:** `options_engine.select_strike(direction, trade_type, spot_price) -> dict`

Selection logic:
```
SWING LONG   → ATM CE or ITM CE (better delta)
SWING SHORT  → ATM PE or ITM PE
SCALP LONG   → ATM CE (high gamma, tight spread)
SCALP SHORT  → ATM PE
HIGH CONF    → ITM (safer, higher cost)
LOW CONF     → OTM (cheaper, more risk)
```

### 5C. Position Sizing

**User Input at startup:** `budget = int(input("Enter max trade budget (INR): "))`

```python
def calculate_qty(budget, option_premium, lot_size=75):
    max_lots = budget // (option_premium * lot_size)
    max_lots = max(1, min(max_lots, 10))  # Floor 1, ceiling 10 lots
    qty = max_lots * lot_size
    cost = qty * option_premium
    return qty, cost, max_lots
```

---

## ═══ STEP 6 — ORDER EXECUTION ═══

**Function:** `trade_executor.place_order(security_id, direction, qty, limit_price, sl, tp) -> dict`

Maps to IndStocks Smart Order API:
```json
POST https://api.indstocks.com/smart/order
{
  "txn_type": "BUY",            // BUY for CE long, BUY for PE long
  "exchange": "NSE",
  "segment": "DERIVATIVE",
  "product": "MARGIN",
  "order_type": "LIMIT",
  "validity": "DAY",
  "security_id": "{option_security_id}",
  "qty": {calculated_qty},
  "limit_price": {atm_premium},
  "sl_trigger_price": {sl_price},
  "tgt_trigger_price": {tp1_price},
  "sl_limit_price": {sl_price - 1},
  "tgt_limit_price": {tp1_price - 1},
  "algo_id": "99999"
}
```

**SL/TP Calculation:**
```
Premium paid = option_premium
SL price     = premium * (1 - 0.30)   → 30% loss on premium
TP1 price    = premium * (1 + 0.60)   → 60% gain on premium
TP2 price    = premium * (1 + 1.00)   → 100% gain (trailing from TP1)
```

---

## ═══ STEP 7 — INDICATOR ENGINE (ALL TRADE TYPES) ═══

The indicator engine is split into three modes, selected based on `trade_type` from LLM.
Each mode uses a different chart timeframe and a different indicator stack.
All indicators computed via `pandas_ta` (pure Python, Termux-safe).

---

### 7A. SCALP MODE — 3-Minute Chart
**Trigger:** `trade_type == "SCALP"`
**Polling:** Every 60 seconds
**Function:** `indicators.compute_scalp_signals(df_3min) -> dict`

#### Indicator Stack

| Indicator | Params | Role | Community Rating |
|-----------|--------|------|-----------------|
| **SuperTrend** | Period=10, Mult=3 | Primary directional filter | ⭐⭐⭐⭐⭐ Most used scalp filter in India |
| **VWAP** | Daily reset | Intraday fair value anchor | ⭐⭐⭐⭐⭐ Institutional benchmark |
| **EMA 9 / 21** | Fast/Slow | Momentum crossover | ⭐⭐⭐⭐ Widely used, slight lag on 3-min |
| **RSI** | Period=14 | Prevent extreme zone entries | ⭐⭐⭐⭐ Universal momentum guard |
| **Volume** | vs 20-bar avg | Entry conviction filter | ⭐⭐⭐⭐⭐ Required for valid breakouts |

#### Tiered Signal Logic (NOT all-AND — prevents over-filtering)

```
# TIER 1 — MUST HAVE BOTH (non-negotiable gates):
SuperTrend direction == trade_direction
price is on correct side of VWAP
  → LONG:  price > VWAP
  → SHORT: price < VWAP

# TIER 2 — NEED AT LEAST 1 OF 2 (momentum confirmation):
EMA9 > EMA21 (for LONG) / EMA9 < EMA21 (for SHORT)
  OR
RSI not at extreme (RSI < 72 for LONG, RSI > 28 for SHORT)

# TIER 3 — NICE TO HAVE (boosts confidence score, not blocking):
Volume > avg * 1.1

# FINAL SIGNAL:
LONG  = TIER1_PASS AND TIER2_PASS (TIER3 adds confidence bonus)
SHORT = TIER1_PASS AND TIER2_PASS (TIER3 adds confidence bonus)
```

**Signal scoring output:**
```python
{
  "direction": "LONG|SHORT|NO_SIGNAL",
  "tier1_pass": True/False,
  "tier2_pass": True/False,
  "volume_confirm": True/False,
  "confidence_score": 0-3          # sum of tiers passed; trade if >= 2
}
```

**Re-entry rules:**
- After TP1 hit: re-score; re-enter only if `confidence_score >= 2`
- Max 3 scalp trades per session to avoid overtrading

---

### 7B. INTRADAY MODE — 15-Minute Chart
**Trigger:** `trade_type == "INTRADAY"`
**Polling:** Every 5 minutes
**Function:** `indicators.compute_intraday_signals(df_15min) -> dict`

#### Indicator Stack

| Indicator | Params | Role | Community Rating |
|-----------|--------|------|-----------------|
| **VWAP** | Daily reset | Primary intraday trend reference | ⭐⭐⭐⭐⭐ |
| **CPR** | Daily pivot, R1, R2, S1, S2 | Magnet levels for price action | ⭐⭐⭐⭐⭐ Hugely popular in Indian markets |
| **EMA 20 / 50** | Fast/Slow | Trend structure on 15-min | ⭐⭐⭐⭐⭐ |
| **MACD** | 12, 26, 9 | Momentum + crossover signal | ⭐⭐⭐⭐⭐ Global gold standard |
| **ADX + DI** | Period=14 | Trend strength gate | ⭐⭐⭐⭐⭐ Filters sideways noise |
| **Bollinger Bands** | 20, 2.0 | Volatility + squeeze detection | ⭐⭐⭐⭐ Entry on band expansion |
| **Volume** | vs 20-bar avg | Breakout confirmation | ⭐⭐⭐⭐⭐ |

#### CPR (Central Pivot Range) Calculation:
```
Pivot  = (Prev_High + Prev_Low + Prev_Close) / 3
BC     = (Prev_High + Prev_Low) / 2          # Bottom Central
TC     = (Pivot - BC) + Pivot               # Top Central
R1     = (2 * Pivot) - Prev_Low
R2     = Pivot + (Prev_High - Prev_Low)
S1     = (2 * Pivot) - Prev_High
S2     = Pivot - (Prev_High - Prev_Low)
```
Narrow CPR (TC-BC < 0.1%) = trending day expected
Wide CPR (TC-BC > 0.3%)   = sideways/reversal day expected

#### Intraday Signal Logic:

```
# GATE (skip trade if failing):
ADX > 25  →  trending market; proceed
ADX < 20  →  sideways; switch to NO_TRADE or reduce size by 50%

# LONG SETUP (need 3 of 4):
1. price > VWAP
2. EMA20 > EMA50
3. MACD line > Signal line AND MACD histogram turning positive
4. price above CPR Top (TC) OR bouncing from CPR Bottom (BC) as support
BONUS: Bollinger Band expanding upward + Volume > avg*1.15

# SHORT SETUP (need 3 of 4):
1. price < VWAP
2. EMA20 < EMA50
3. MACD line < Signal line AND MACD histogram turning negative
4. price below CPR Bottom (BC) OR rejected from CPR Top (TC) as resistance
BONUS: Bollinger Band expanding downward + Volume > avg*1.15
```

---

### 7C. SWING MODE — Daily Chart
**Trigger:** `trade_type == "SWING"`
**Polling:** Once at 9:35 AM (entry decision), then check at 12:00 PM and 3:00 PM
**Function:** `indicators.compute_swing_signals(df_daily, df_weekly) -> dict`

#### Indicator Stack

| Indicator | Params | Role | Community Rating |
|-----------|--------|------|-----------------|
| **EMA 50 / 200** | Daily | Golden/Death Cross — macro trend | ⭐⭐⭐⭐⭐ Most watched by institutions |
| **MACD** | 12, 26, 9 on Daily | Trend momentum confirmation | ⭐⭐⭐⭐⭐ |
| **ADX** | Period=14 | Trend strength qualifier | ⭐⭐⭐⭐⭐ |
| **Ichimoku Cloud** | 9, 26, 52 | Multi-dimensional trend system | ⭐⭐⭐⭐⭐ Most complete single indicator |
| **RSI** | Period=14 Daily | Overbought/oversold + divergence | ⭐⭐⭐⭐ |
| **Weekly Pivot** | Prev week H/L/C | Key S/R levels for the week | ⭐⭐⭐⭐⭐ |

#### Ichimoku Rules (simplified for signal generation):
```
BULLISH when ALL:
  price > Kumo (Cloud)
  Tenkan-sen (9) > Kijun-sen (26)
  Chikou Span above price from 26 periods ago
  Cloud ahead is GREEN (Senkou A > Senkou B)

BEARISH when ALL:
  price < Kumo (Cloud)
  Tenkan-sen < Kijun-sen
  Chikou Span below price from 26 periods ago
  Cloud ahead is RED (Senkou B > Senkou A)

INSIDE CLOUD = ambiguous; NO_TRADE for swing
```

#### Swing Signal Logic:
```
# MACRO FILTER (EMA 50/200 — check once daily):
EMA50 > EMA200  → Bull regime  → only take LONG swings
EMA50 < EMA200  → Bear regime  → only take SHORT swings
EMA50 ≈ EMA200  → Neutral      → reduce size 50%, require HIGH confidence

# ENTRY SIGNAL (need Ichimoku + MACD agreement):
LONG  = EMA_regime BULL + Ichimoku BULLISH + MACD histogram > 0 + ADX > 20 + RSI > 50
SHORT = EMA_regime BEAR + Ichimoku BEARISH + MACD histogram < 0 + ADX > 20 + RSI < 50

# WEEKLY PIVOT CONTEXT (attach to LLM for open analysis):
If price near Weekly R1/R2 → caution on LONG entries (resistance ahead)
If price near Weekly S1/S2 → caution on SHORT entries (support ahead)
```

---

### 7D. INDICATOR SUMMARY TABLE (Quick Reference)

| Mode | Timeframe | Must-Pass | Confirmation | Context |
|------|-----------|-----------|-------------|---------|
| SCALP | 3-min | SuperTrend + VWAP side | EMA cross OR RSI not extreme | Volume 1.1x |
| INTRADAY | 15-min | ADX > 25 + VWAP side | MACD + EMA20/50 + CPR level | BB expansion + Volume |
| SWING | Daily | EMA 50/200 regime + Ichimoku | MACD histogram + ADX > 20 | RSI zone + Weekly pivot |

---

## ═══ STEP 8 — RISK MANAGEMENT & AUTO-EXIT ═══

**Function:** `risk_manager.monitor_position(order_id, entry_premium) -> None`

Run in a loop (every 60s) after trade entry:

```python
while position_open:
    current_premium = fetch_ltp(security_id)
    pnl_pct = (current_premium - entry_premium) / entry_premium

    # Hard stop loss
    if pnl_pct <= -0.30:
        place_exit_order("STOP_LOSS")
        break

    # Trailing stop after TP1
    if pnl_pct >= 0.60:
        peak_premium = max(peak_premium, current_premium)
        trailing_sl = peak_premium * (1 - 0.20)
        if current_premium <= trailing_sl:
            place_exit_order("TRAILING_STOP")
            break

    # Hard target
    if pnl_pct >= 1.00:
        place_exit_order("TAKE_PROFIT")
        break

    # EOD forced exit at 15:15
    if current_time >= "15:15":
        place_exit_order("EOD_EXIT")
        break

    time.sleep(60)
```

---

## ═══ SCHEDULER FLOW (main.py) ═══

```
09:00 → [JOB 1] fetch_historical_ohlcv() + fetch_nifty_news()  [parallel threads]
09:00 → [JOB 2] analyze_premarket(historical_data, news)         [LLM call #1]
09:30 → [JOB 3] fetch_first_30min_candle()
09:30 → [JOB 4] analyze_market_open(premarket_result, open_data) [LLM call #2]
09:35 → [JOB 5] fetch_options_chain() + select_strike() + calculate_qty()
09:36 → [JOB 6] place_order() if confidence != LOW
09:37 → [JOB 7] monitor_position() loop until exit or 15:15
15:15 → [SAFETY] force_close_all_positions()
```

**Implementation:** Use `schedule` library + `threading.Thread` for parallel jobs

---

## ═══ STARTUP SEQUENCE (main.py) ═══

```python
# On script start, collect user inputs BEFORE market opens
budget = int(input("Enter max trade budget in INR (e.g. 50000): "))
risk_profile = input("Risk profile [conservative/moderate/aggressive]: ").lower()
confirm = input("Live trading? Type YES to confirm, else paper trade: ")
LIVE_TRADING = (confirm == "YES")

# Adjust SL/TP by risk profile
if risk_profile == "conservative":
    DEFAULT_SL_PCT = 0.20; DEFAULT_TP_PCT = 0.40
elif risk_profile == "aggressive":
    DEFAULT_SL_PCT = 0.40; DEFAULT_TP_PCT = 0.80
```

---

## ═══ GOOGLE GENAI INTEGRATION ═══

```python
from google import genai
from google.genai import types

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def call_gemini(prompt: str, max_tokens=1024) -> str:
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.2,         # Low temp for consistent JSON
            response_mime_type="application/json"
        )
    )
    return response.text
```

---

## ═══ INDSTOCKS API REFERENCE ═══

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/market/quotes/full?scrip-codes=NSE_3045` | GET | Full quote with OHLC |
| `/market/quotes/ltp?scrip-codes=NSE_3045` | GET | Last traded price only |
| `/market/quotes/mkt?scrip-codes=NSE_3045` | GET | Market depth |
| `/portfolio/holdings` | GET | Current holdings |
| `/portfolio/positions?segment=derivative&product=margin` | GET | Open positions |
| `/smart/order` | POST | Place bracket/cover order |

**Auth Header for all calls:** `Authorization: Bearer {INDSTOCKS_TOKEN}`

**Rate limit assumption:** max 1 req/sec; add `time.sleep(1)` between batch calls

---

## ═══ TERMUX-SPECIFIC NOTES ═══

```bash
# Initial Termux setup commands
pkg update && pkg upgrade
pkg install python clang libxml2 libxslt
pip install requests pandas numpy schedule google-generativeai \
    python-dotenv feedparser beautifulsoup4 lxml

# TA-Lib alternative (no C compiler needed on Termux):
pip install pandas-ta            # Pure Python TA library, use instead of ta-lib

# Run bot
python main.py

# Keep running after screen off
termux-wake-lock
nohup python main.py > trading.log 2>&1 &
```

**Use `pandas_ta` NOT `ta-lib`** — ta-lib requires native C compilation which often fails on Termux arm64.

All indicators (SuperTrend, VWAP, EMA, RSI, MACD, ADX, Bollinger Bands, Ichimoku) are available in `pandas_ta`. CPR must be computed manually (simple arithmetic — see Step 7B).

---

## ═══ ERROR HANDLING RULES ═══

| Error | Action |
|-------|--------|
| LLM returns invalid JSON | Retry once; if fail → skip trade, log |
| IndStocks API timeout | Retry 3x with 5s backoff; then abort |
| LLM says NO_TRADE or LOW confidence | Skip trade; run again next day |
| Network error during active trade | Switch to fallback SL via existing bracket order |
| Budget insufficient for 1 lot | Print warning; do not trade |
| Market holiday | Detect via NSE holiday list; skip all jobs |

---

## ═══ PAPER TRADING MODE ═══

When `LIVE_TRADING = False`:
- All order placements → print to terminal instead of API call
- Track simulated PnL in memory
- All monitoring logic still runs using real LTP data
- Print trade summary at 15:30

---

## ═══ OUTPUT FORMAT (Terminal Logs) ═══

```
[09:00] 📊 Fetching historical data...
[09:01] 📰 News fetched: 18 headlines
[09:02] 🤖 LLM Pre-market: BEARISH WEEKLY | NEUTRAL HOURLY | Strategy: SCALP_SHORT
[09:30] 📈 30-min open: -0.4% | Low vol | Gap-down 0.2%
[09:31] 🤖 LLM Open: SCALP_SHORT confirmed | Entry: 22200–22250 | SL: 22350 | TP: 21950
[09:32] 🎯 Selected: NIFTY 22200 PE | Premium: ₹85 | Qty: 75 | Cost: ₹6375
[09:33] ✅ Order placed | OrderID: 78234
[09:50] 📉 PnL: +18% | Peak: ₹100 | Trail SL: ₹80
[10:12] 🏁 TRAILING STOP hit | Exit: ₹79 | P&L: +₹(79-85)*75 = -₹450
```

---

## ═══ IMPLEMENTATION ORDER FOR DEVELOPER ═══

Build in this sequence to ensure testability at each stage:

1. `config.py` + `logger.py` — foundation
2. `market_data.py` — test with live IndStocks token
3. `news_fetcher.py` — test RSS parsing
4. `indicators.py` — unit test with sample OHLCV data
5. `llm_analyst.py` — test prompts with mocked data
6. `options_engine.py` — test strike selection logic offline
7. `trade_executor.py` — test in PAPER mode first
8. `risk_manager.py` — test with simulated price stream
9. `scheduler.py` — wire all jobs together
10. `main.py` — final integration + user input loop

---

*Plan version: 2.0 | Generated for Nifty 50 Options | IndStocks + Google GenAI + Termux*
