import pandas as pd
import requests
import os
from .config import INDSTOCKS_BASE, NIFTY_SCRIP_CODE
from .logger import log

def get_auth_headers():
    token = os.getenv("INDSTOCKS_TOKEN", "")
    return {"Authorization": token}

def _fetch_indstocks_chart(interval='5minute', days_back=1):
    """
    Fetches chart data from INDMoney API.
    Valid intervals: 1minute, 5minute, 15minute, 30minute, 60minute, 1day, 1week, 1month
    """
    try:
        import time
        end_time = int(time.time() * 1000)
        start_time = end_time - (days_back * 24 * 60 * 60 * 1000)
        url = f"{INDSTOCKS_BASE}/market/historical/{interval}?scrip-codes={NIFTY_SCRIP_CODE}&start_time={start_time}&end_time={end_time}"
        res = requests.get(url, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            data = res.json()
            scrip_data = data.get('data', {}).get(NIFTY_SCRIP_CODE, {})
            candles = scrip_data.get('candles', [])
            if candles:
                df = pd.DataFrame(candles)
                df.rename(columns={'ts': 'Timestamp', 'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume'}, inplace=True)
                # Convert epoch seconds to datetime
                df['Date'] = pd.to_datetime(df['Timestamp'], unit='s')
                df.set_index('Date', inplace=True)
                return df
            else:
                print(f"INDMoney returned 200 OK but no candle data found: {data}")
                log.warning(f"INDMoney returned 200 OK but no candle data found: {data}")
        else:
            print(f"INDMoney Chart API Error: {res.status_code} - {res.text}")
            log.error(f"INDMoney Chart API Error: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Exception fetching INDMoney Chart: {e}")
        log.error(f"Exception fetching INDMoney Chart: {e}")
    return pd.DataFrame()

def fetch_historical_ohlcv(timeframes=['1mo', '1wk', '1h']):
    """
    Fetches historical OHLCV data directly from INDMoney API.
    """
    data = {}
    
    try:
        if '1mo' in timeframes:
            data['1mo'] = _fetch_indstocks_chart(interval='1month', days_back=180)
            
        if '1wk' in timeframes:
            data['1wk'] = _fetch_indstocks_chart(interval='1week', days_back=90)
            
        if '1h' in timeframes:
            data['1h'] = _fetch_indstocks_chart(interval='60minute', days_back=5)
            
    except Exception as e:
        log.error(f"❌ Error fetching historical INDMoney data: {e}")
        
    return data

def compress_ohlcv_to_string(df, timeframe, n_candles=5):
    """
    Compress DataFrame to string to save LLM tokens.
    Format: DATE|O|H|L|C|V|CHG%
    """
    if df is None or df.empty:
        print("Failed to fetch data from INDMoney. Please check if your INDSTOCKS_TOKEN in .env is valid and active.")
        return f"NIFTY50 | {timeframe} | NO DATA"
        
    df = df.tail(n_candles).copy()
    
    # Calculate % change
    df['CHG%'] = df['Close'].pct_change() * 100
    df['CHG%'] = df['CHG%'].fillna(0)
    
    lines = [f"NIFTY50 | {timeframe} | LAST {len(df)} CANDLES"]
    lines.append("DATE|O|H|L|C|V|CHG%")
    
    for idx, row in df.iterrows():
        date_str = idx.strftime('%Y-%m-%d %H:%M') if timeframe in ['1h', '1m', '3m', '15m'] else idx.strftime('%Y-%m-%d')
        o = int(row['Open'])
        h = int(row['High'])
        l = int(row['Low'])
        c = int(row['Close'])
        v = f"{row['Volume']/1000:.1f}K" if row['Volume'] < 1000000 else f"{row['Volume']/1000000:.1f}M"
        chg = f"{row['CHG%']:.2f}%"
        
        lines.append(f"{date_str}|{o}|{h}|{l}|{c}|{v}|{chg}")
        
    return "\n".join(lines)

def fetch_intraday_data(interval='5minute', days_back=1):
    """
    Fetches 5m intraday data directly from INDMoney for SCALP.
    """
    return _fetch_indstocks_chart(interval=interval, days_back=days_back)

def fetch_first_30min_candle():
    """
    Fetches the first 30-min data of the day from INDMoney.
    """
    df = _fetch_indstocks_chart(interval='30minute', days_back=1)
    if not df.empty:
        row = df.iloc[0]
        return {
            "open": float(row['Open']),
            "high": float(row['High']),
            "low": float(row['Low']),
            "current": float(row['Close']),
            "volume": float(row['Volume']),
            "pct_change": ((row['Close'] - row['Open']) / row['Open']) * 100
        }
    return {}

def fetch_ltp(scrip_code=NIFTY_SCRIP_CODE):
    """
    Fetch Last Traded Price directly from INDMoney API.
    """
    try:
        url = f"{INDSTOCKS_BASE}/market/quotes/ltp?scrip-codes={scrip_code}"
        res = requests.get(url, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            data = res.json()
            return float(data.get('data', {}).get(scrip_code, {}).get('live_price', 0))
        else:
            log.error(f"❌ INDMoney LTP API Error: {res.status_code} - {res.text}")
    except Exception as e:
        log.error(f"❌ Exception fetching INDMoney LTP: {e}")
    return 0.0


def fetch_finnifty_direction() -> float:
    """
    Fetches FinNifty (Nifty Financial Services) last 2 candles to determine
    if financials are trending in the same direction as a Nifty signal.

    Returns:
        +1.0  — FinNifty bullish  (close > open, or price rising over 2 bars)
        -1.0  — FinNifty bearish
         0.0  — flat / API unavailable (neutral — no penalty, no boost)

    Usage: pass as fnf_direction to compute_multi_rating for 10th signal.
    Financials (HDFC, ICICI, Kotak, Axis) drive ~40% of Nifty weight.
    If FinNifty diverges from Nifty signal → likely fake breakout → penalise score.
    """
    from .config import FINNIFTY_SCRIP_CODE
    try:
        import time
        end_time   = int(time.time() * 1000)
        start_time = end_time - (2 * 60 * 60 * 1000)   # last 2 hours = enough for 2 candles
        url = (f"{INDSTOCKS_BASE}/market/historical/5minute"
               f"?scrip-codes={FINNIFTY_SCRIP_CODE}"
               f"&start_time={start_time}&end_time={end_time}")
        res = requests.get(url, headers=get_auth_headers(), timeout=4)
        if res.status_code == 200:
            candles = (res.json().get('data', {})
                                 .get(FINNIFTY_SCRIP_CODE, {})
                                 .get('candles', []))
            if len(candles) >= 2:
                last  = candles[-1]
                prev  = candles[-2]
                close_now  = float(last.get('c', 0))
                close_prev = float(prev.get('c', 0))
                open_now   = float(last.get('o', close_now))
                if close_now > open_now and close_now > close_prev:
                    return +1.0   # both bar and recent trend bullish
                if close_now < open_now and close_now < close_prev:
                    return -1.0   # both bar and recent trend bearish
            # Single candle fallback
            if candles:
                c = candles[-1]
                diff = float(c.get('c', 0)) - float(c.get('o', 0))
                if   diff > 0: return +0.5
                elif diff < 0: return -0.5
    except Exception as ex:
        log.debug(f"FinNifty fetch failed (non-critical): {ex}")
    return 0.0   # neutral on any error — never hard-block on unavailable data


# ─────────────────────────────────────────────────────────────────────────────
#  BLACK-SCHOLES IV (pure Python — no scipy, works on Termux)
# ─────────────────────────────────────────────────────────────────────────────
import math as _math

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + _math.erf(x / _math.sqrt(2.0)))

def _norm_pdf(x: float) -> float:
    return _math.exp(-0.5 * x * x) / _math.sqrt(2.0 * _math.pi)

def _bs_price(S: float, K: float, T: float, r: float, sigma: float, call: bool = True) -> float:
    """Black-Scholes option price. S=spot, K=strike, T=years, r=risk-free, sigma=IV."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if call else max(0.0, K - S)
    d1 = (_math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * _math.sqrt(T))
    d2 = d1 - sigma * _math.sqrt(T)
    if call:
        return S * _norm_cdf(d1) - K * _math.exp(-r * T) * _norm_cdf(d2)
    return K * _math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

def _bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Vega — derivative of option price w.r.t. sigma."""
    if T <= 0 or sigma <= 0:
        return 1e-8
    d1 = (_math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * _math.sqrt(T))
    return S * _norm_pdf(d1) * _math.sqrt(T)

def compute_iv_from_premium(S: float, K: float, T: float, market_price: float,
                             r: float = 0.065, call: bool = True) -> float | None:
    """
    Newton-Raphson IV solver. Typically converges in 5-10 iterations.
    Returns annualised IV (e.g. 0.18 = 18%) or None if no convergence.
    """
    sigma = 0.25  # initial guess
    for _ in range(60):
        price = _bs_price(S, K, T, r, sigma, call)
        vega  = _bs_vega(S, K, T, r, sigma)
        diff  = market_price - price
        if abs(diff) < 1e-4:
            return sigma
        if abs(vega) < 1e-8:
            return None
        sigma += diff / vega
        sigma = max(0.01, min(sigma, 10.0))
    return sigma if abs(_bs_price(S, K, T, r, sigma, call) - market_price) < 0.05 else None


def _next_expiry_years() -> float:
    """Days to next Thursday (weekly Nifty expiry) as fraction of year."""
    import time
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    days_ahead = (3 - now.weekday()) % 7   # 3 = Thursday
    if days_ahead == 0 and now.hour >= 15:
        days_ahead = 7
    T = max(days_ahead + (15 - now.hour) / 24, 0.5 / 365)
    return T / 365


def _atm_strike(spot: float) -> int:
    """Round to nearest 50 (Nifty weekly strikes)."""
    return int(round(spot / 50.0) * 50)


# ── 15-min module-level cache ─────────────────────────────────────────────────
_ivr_cache: dict = {"ts": 0.0, "result": {"ivr": 50.0, "iv": 0.0, "signal": 0.0}}
_IVR_TTL_SEC = 15 * 60   # refresh at most every 15 minutes

# IV history file (persists across sessions so IVR improves daily)
import pathlib as _pathlib, json as _json

_IV_HISTORY_PATH = _pathlib.Path(__file__).parent.parent / "models" / "iv_history.json"


def _load_iv_history() -> list[float]:
    try:
        if _IV_HISTORY_PATH.exists():
            return _json.loads(_IV_HISTORY_PATH.read_text())
    except Exception:
        pass
    return []


def _append_iv_history(iv: float) -> list[float]:
    hist = _load_iv_history()
    hist.append(round(iv, 5))
    hist = hist[-250:]   # keep last 250 observations (~60 trading days × 4 polls)
    try:
        _IV_HISTORY_PATH.parent.mkdir(exist_ok=True)
        _IV_HISTORY_PATH.write_text(_json.dumps(hist))
    except Exception:
        pass
    return hist


def fetch_iv_rank() -> dict:
    """
    Fetches live ATM option premium, computes IV via Black-Scholes, and returns
    IV Rank (percentile vs last 60 days).

    Safety guarantees:
      - Timeout: 2 seconds (not 5) — never blocks poll cycle
      - Full try/except — ANY error returns neutral {'ivr':50, 'iv':0, 'signal':0.0}
      - 15-minute cache — at most 1 extra API call per 15 min, not per poll
      - Signal range: [-0.15, +0.10] — too small to break a trade on its own

    Returns dict: {ivr: float (0-100), iv: float (annualised), signal: float}
    signal: +0.10 if IVR<25 (cheap premium, expansion likely)
             0.00 if 25-65 (neutral)
            -0.15 if IVR>75 (expensive premium, crush risk)
    """
    import time
    neutral = {"ivr": 50.0, "iv": 0.0, "signal": 0.0}

    # ── Cache check ────────────────────────────────────────────────────────────
    if time.time() - _ivr_cache["ts"] < _IVR_TTL_SEC:
        return _ivr_cache["result"]

    try:
        # ── Step 1: Get spot price ────────────────────────────────────────────
        spot = fetch_ltp(NIFTY_SCRIP_CODE)
        if not spot or spot <= 0:
            return neutral

        K  = _atm_strike(spot)
        T  = _next_expiry_years()
        r  = 0.065     # RBI repo rate approximation

        # ── Step 2: Fetch ATM CALL LTP (2-second timeout) ────────────────────
        # We use the FNO instruments CSV (already loaded/cached in options_engine)
        # to find the ATM CALL scrip code, then fetch its LTP.
        # Import here to avoid circular imports at module load time.
        from .options_engine import _get_instruments
        instr = _get_instruments()

        if instr.empty:
            return neutral

        import pandas as _pd
        import datetime as _dt
        from datetime import timezone as _tz, timedelta as _td

        today = _dt.date.today()
        atm_calls = instr[
            (instr.get('NAME', instr.get('SYMBOL', _pd.Series(dtype=str))).str.upper().str.contains('NIFTY', na=False)) &
            (instr.get('OPTION_TYPE', instr.get('INSTRUMENT_TYPE', _pd.Series(dtype=str))).str.upper().str.contains('CE', na=False)) &
            (instr['EXPIRY_DATE'] >= _pd.Timestamp(today))
        ].copy() if not instr.empty else _pd.DataFrame()

        if atm_calls.empty:
            return neutral

        strike_col = 'STRIKE_PRICE' if 'STRIKE_PRICE' in atm_calls.columns else (
                     'STRIKE'       if 'STRIKE'       in atm_calls.columns else None)
        sec_col    = 'SECURITY_ID'  if 'SECURITY_ID'  in atm_calls.columns else (
                     'SCRIP_CODE'   if 'SCRIP_CODE'   in atm_calls.columns else None)

        if not strike_col or not sec_col:
            return neutral

        atm_calls['strike_dist'] = (atm_calls[strike_col] - K).abs()
        best = atm_calls.nsmallest(1, 'strike_dist')
        if best.empty:
            return neutral

        atm_scrip = str(best.iloc[0][sec_col])
        call_url  = f"{INDSTOCKS_BASE}/market/quotes/ltp?scrip-codes={atm_scrip}"
        resp      = requests.get(call_url, headers=get_auth_headers(), timeout=2)

        if resp.status_code != 200:
            return neutral

        ltp_data     = resp.json()
        call_premium = float(ltp_data.get('data', {}).get(atm_scrip, {}).get('live_price', 0))

        if call_premium <= 0:
            return neutral

        # ── Step 3: Compute IV ────────────────────────────────────────────────
        iv = compute_iv_from_premium(spot, K, T, call_premium, r=r, call=True)
        if iv is None or iv <= 0:
            return neutral

        # ── Step 4: Compute IVR ───────────────────────────────────────────────
        hist = _append_iv_history(iv)

        if len(hist) < 10:
            # Not enough history yet — be neutral, not penalising
            result = {"ivr": 50.0, "iv": round(iv, 4), "signal": 0.0}
        else:
            ivr = sum(1 for h in hist if h < iv) / len(hist) * 100  # percentile rank
            if   ivr < 25:  signal = +0.10    # cheap premium, likely to expand
            elif ivr > 75:  signal = -0.15    # expensive premium, crush risk
            else:           signal =  0.00    # neutral
            result = {"ivr": round(ivr, 1), "iv": round(iv, 4), "signal": signal}

        # ── Step 5: Update cache ──────────────────────────────────────────────
        _ivr_cache["ts"]     = time.time()
        _ivr_cache["result"] = result
        log.debug(f"IVR updated: IV={iv:.1%} IVR={result['ivr']:.0f} signal={result['signal']:+.2f}")
        return result

    except Exception as ex:
        # SILENT FAIL — poll cycle must never break because of IV fetch
        log.debug(f"IVR fetch skipped (non-critical): {ex}")
        return neutral
