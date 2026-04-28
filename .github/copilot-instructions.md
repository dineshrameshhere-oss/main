# Copilot Instructions for Nifty 50 Options Trading Bot

## Architecture Overview

This is an **automated algorithmic options trading bot** for Nifty 50 that operates in paper and live modes. It has two independent trading strategies:

1. **Scalping Bot** (Scalper): 5-minute candle analysis with LLM (Gemini) + technical indicators for 5–10 minute trades
2. **Deep Learning Intraday Bot** (DL Intraday): 15-minute candle analysis with scikit-learn ML models for 2-hour hold positions

**Key Constraint**: Runs on Android Termux with ₹2000 max capital — designs prioritize low RAM and zero file I/O overhead.

## Critical Data Flows

### Scalping Bot Flow (`scheduler.py` → `main.py --startScalp`)
1. **09:00 IST** → Fetch 1-month/1-week/1-hour historical data → Compress to text → Send to Gemini for pre-market bias
2. **09:30 IST** → Fetch first 30-min candle → Confirm directional signal with Gemini
3. **Every 5 minutes** (09:35–15:15) → Compute multi-indicator rating (SuperTrend, RSI, Volume, VWAP, FinNifty confirmation)
4. **Signal Threshold**: If rating ≥ +0.45 (STRONG_BUY) → Select ATM/ITM/OTM strike → Place order → Monitor via threading
5. **Monitor Thread** (30-sec polling) → Fetch live LTP from INDMoney API → Check SL/TP/TSL/daily loss circuit → Auto-exit

### Deep Learning Intraday Flow (`intraday_scheduler.py` → `main.py --startIntraday`)
1. **Pre-market** → No training; uses pre-trained models from `models/` (see **Models Section**)
2. **Every 15 minutes** (09:30–15:15) → Fetch daily candles (resampled to 15m internally) → Call `compute_dl_rating()` → Get direction/score
3. **If score high enough** → Select strike → Place order → Monitor for max 2-hour hold with stepped trailing SL
4. **Exiting** → Manual close OR time-based OR SL/TP hit OR daily loss limit

## Core Modules & Dependencies

| Module | Purpose | Key Design Pattern |
|--------|---------|-------------------|
| `config.py` | **All constants live here** — never hardcode elsewhere. API endpoints, lot sizes, thresholds, timeframes. | Single source of truth |
| `market_data.py` | Fetch historical OHLCV via INDMoney API; compress multi-timeframe data to string for LLM | Stateless API calls |
| `llm_analyst.py` | Call Gemini with structured prompts; parse JSON responses for bias/strategy | Low temp (0.2) for consistent JSON |
| `indicators.py` | SuperTrend, RSI, MACD, ADX, ORB, VWAP, multi-indicator rating logic | All scale-invariant (%) |
| `options_engine.py` | Select strike (ATM ±3 for aggressive scalps), size position by budget, compute PCR | Instruments cached in memory |
| `trade_executor.py` | Paper vs. live order placement; JSON wallet for paper mode (no file I/O per trade) | Dual-mode: live API XOR paper sim |
| `risk_manager.py` | Monitor position; fetch live LTP; apply hard SL, stepped TSL, daily loss circuit | 30-sec poll thread; timeout-safe |
| `scheduler.py` (scalp) | Orchestrate 5m polling, multi-indicator rating, state machine for position management | `BotState` class + threading locks |
| `intraday_scheduler.py` | Same orchestration but 15m polling; integrates ML engine | Reuses `BotState` from `scheduler.py` |
| `ml_trainer.py` | Trains 3 scikit-learn models (direction, volume, vega) on 2 years 15m data | Walk-forward split (no shuffle) |
| `dl_engine.py` | Load pre-trained models; compute ratings at market time | Uses lag features (10 bars = 2.5h) |
| `logger.py` | Colored console output + plain file log (survives Termux session restart) | No pop-ups; Agg backend only |

## Key Patterns & Conventions

### 1. **IST Timezone (No tzdata)**
All times are IST (+5:30) calculated via `datetime.timezone(timedelta(hours=5, minutes=30))`. Never import `pytz`.

### 2. **Multi-Indicator Rating Score**
Computed in `indicators.py::compute_multi_rating()`. Returns normalized score [-1, +1]:
- Combines SuperTrend direction, RSI, Volume surge, VWAP proximity
- Thresholds: STRONG_BUY ≥ +0.45, BUY ≥ +0.2, SELL ≤ -0.45
- Afternoon relaxation: if 0 trades by 12:30, relax to +0.40 (avoid missed opportunities)

### 3. **Paper Mode Trade State**
State stored in `BotState` (thread-safe dict) — paper balance written to `paper_balance.json` only on order entry/close.  
**Never** persist state mid-trade; only checkpoint at trade boundaries to avoid Termux RAM pressure.

### 4. **Live Mode Dual-Code Paths**
Every trade function checks `live=True/False` parameter:
```python
if not live:
    log.info(f"🟢 [PAPER TRADE] ...")
    return paperOrder
else:
    # Call INDMoney API ...
    log.info(f"✅ [LIVE TRADE] ...")
    return liveOrder
```

### 5. **ML Model Lifecycle**
- **Training** (rare): `python -m algo_trading.ml_trainer` — fetches 2 years via INDMoney, trains, saves to `models/`
- **Runtime** (always): `dl_engine.py` loads pickled models on startup, reuses all session
- **Features**: 10-bar lag window (scale-invariant %), balanced class weights, max_depth=8 overfitting guards

### 6. **FinNifty as Confirmation**
Scalper can optionally fetch FinNifty direction as secondary confirmation signal. See `fetch_finnifty_direction()` in `market_data.py`.

### 7. **Instruments Cache**
Options engine loads INDMoney FNO instruments CSV once per session and caches in `_INSTRUMENTS_DF`. Expiry dates parsed on load.

## Common Workflows

### Running Locally (Paper Mode)
```bash
# Interactive mode (prompts for live/intraday choice)
python -m algo_trading.main

# Direct: Scalping paper mode
python -m algo_trading.main --startScalp

# Direct: Intraday DL paper mode
python -m algo_trading.main --startIntraday
```

### Running on Android Termux (Live)
```bash
# Confirm with "YES-LIVE" to engage real money
python -m algo_trading.main --live --intraday
```

### Retraining ML Models
```bash
python -m algo_trading.ml_trainer
# Saves 3 pickles to `models/`: direction_model.pkl, vol_model.pkl, vega_model.pkl
```

### Debugging State
Check `logs/bot_YYYYMMDD.log` for all decisions (both live and paper). Colored console shows real-time signal.

## Integration Points & External APIs

| Service | Endpoint | Key Method | Auth |
|---------|----------|------------|------|
| **INDMoney/INDStocks** | `https://api.indstocks.com` | Market data (historical OHLCV), live LTP, order placement | `.env` token |
| **Google Gemini** | GenAI SDK | Pre-market/market-open analysis, JSON bias responses | `GEMINI_API_KEY` env var |

**Critical**: Both API keys must be in `.env` at project root. Missing keys fall back to dummy signals (no trades).

## Testing & Validation

- **Paper mode** simulates trades in memory; balance persists to `paper_balance.json`
- **Backtest** (`backtest.py`) simulates 60 days of scalp trades with real NSE Bhavcopy option premiums
  - **Important**: Uses end-of-day closing premiums; intraday pricing estimated via delta + theta decay model
  - Each day, fetches ATM CE/PE close price from NSE archives; scales intraday moves via Black-Scholes greeks
  - Applies realistic theta decay (~-5 Rs/day for ATM options) and bid-ask spread (+1.5% entry slip)
  - Expected results: 6-12% monthly on ₹5000 capital (60+ trades, 60% win rate)
- **Inspect trades** via logs: `tail -f logs/bot_$(date +%Y%m%d).log`
- **Validate JSON parsing** from Gemini: `llm_analyst.py` catches `json.JSONDecodeError` and logs fallback
- **No unit tests** (legacy project); rely on end-to-end paper runs and backtest validation

## Pitfalls & Safety Nets

| Issue | Guard |
|-------|-------|
| Missing `.env` | API calls return None; no trades placed (graceful degrade) |
| Market closed | Scheduler jobs don't fire outside 09:00–15:30 IST |
| Low balance | All trade functions check `current_balance < 1500` and abort |
| Runaway positions | Hard SL always applies; max hold = 120 min (intraday); daily loss circuit at -15% |
| Threading deadlock | `BotState` uses threading.Lock(); monitor thread times out at 4 sec API calls |
| Out-of-memory | Paper state in-memory only; large DataFrames dropped post-computation |

## Key Files to Know

- **Start here**: [main.py](../algo_trading/main.py) — entry point, banner, live/paper/intraday selection
- **Thresholds live here**: [config.py](../algo_trading/config.py) — all constants (RATING_STRONG_BUY, lot size, times, etc.)
- **Orchestration**: [scheduler.py](../algo_trading/scheduler.py) (scalp) or [intraday_scheduler.py](../algo_trading/intraday_scheduler.py) (DL)
- **Indicator math**: [indicators.py](../algo_trading/indicators.py) — SuperTrend, multi-rating (most complex)
- **Trade lifecycle**: [trade_executor.py](../algo_trading/trade_executor.py) (place) + [risk_manager.py](../algo_trading/risk_manager.py) (monitor)
- **LLM integration**: [llm_analyst.py](../algo_trading/llm_analyst.py) → prompt engineering + JSON parsing
- **ML models**: [ml_trainer.py](../algo_trading/ml_trainer.py) (train) + [dl_engine.py](../algo_trading/dl_engine.py) (infer)
