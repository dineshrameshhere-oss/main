import pandas as pd
import numpy as np
from .logger import log
from .config import (
    SUPERTREND_PERIOD, SUPERTREND_MULT,
    EMA_FAST, EMA_SLOW, RSI_PERIOD,
    VOLUME_MULT_SCALP, RSI_OVERBOUGHT, RSI_OVERSOLD
)

# ─────────────────────────────────────────────────────────────────────────────
#  SUPERTREND
# ─────────────────────────────────────────────────────────────────────────────
def compute_supertrend(df: pd.DataFrame, period: int = SUPERTREND_PERIOD, mult: float = SUPERTREND_MULT):
    """Returns direction Series: -1 = bullish (price above), +1 = bearish."""
    try:
        high  = df['High'];  low = df['Low'];  close = df['Close']
        prev_close = close.shift(1)
        tr  = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        hl2 = (high + low) / 2
        upper_band = hl2 + mult * atr
        lower_band = hl2 - mult * atr

        supertrend = pd.Series(index=df.index, dtype=float)
        direction  = pd.Series(index=df.index, dtype=int)

        for i in range(1, len(df)):
            if upper_band.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]:
                upper_band.iloc[i] = upper_band.iloc[i]
            else:
                upper_band.iloc[i] = upper_band.iloc[i - 1]

            if lower_band.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]:
                lower_band.iloc[i] = lower_band.iloc[i]
            else:
                lower_band.iloc[i] = lower_band.iloc[i - 1]

            if pd.isna(supertrend.iloc[i - 1]):
                direction.iloc[i] = 1
            elif supertrend.iloc[i - 1] == upper_band.iloc[i - 1]:
                direction.iloc[i] = -1 if close.iloc[i] > upper_band.iloc[i] else 1
            else:
                direction.iloc[i] = 1 if close.iloc[i] < lower_band.iloc[i] else -1

            supertrend.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == -1 else upper_band.iloc[i]

        return direction   # -1 = bullish, +1 = bearish

    except Exception as e:
        log.warning(f"SuperTrend error: {e}")
        return pd.Series(0, index=df.index)


# ─────────────────────────────────────────────────────────────────────────────
#  ATR
# ─────────────────────────────────────────────────────────────────────────────
def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    try:
        prev_close = df['Close'].shift(1)
        tr = pd.concat([df['High'] - df['Low'],
                        (df['High'] - prev_close).abs(),
                        (df['Low']  - prev_close).abs()], axis=1).max(axis=1)
        return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])
    except Exception as e:
        log.warning(f"ATR error: {e}")
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  ADX  (trend strength — keeps us out of choppy markets)
# ─────────────────────────────────────────────────────────────────────────────
def compute_adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Returns a Series of ADX values (0-100). >25 = trending, <20 = choppy."""
    try:
        high  = df['High'];  low = df['Low'];  close = df['Close']
        prev_high  = high.shift(1)
        prev_low   = low.shift(1)
        prev_close = close.shift(1)

        up_move   = high - prev_high
        down_move = prev_low - low

        plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)

        atr_s    = tr.ewm(alpha=1/period, adjust=False).mean()
        plus_di  = 100 * pd.Series(plus_dm,  index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr_s
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr_s

        dx  = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        return adx.clip(0, 100)
    except Exception as e:
        log.warning(f"ADX error: {e}")
        return pd.Series(25.0, index=df.index)   # assume neutral if fails


# ─────────────────────────────────────────────────────────────────────────────
#  MACD histogram  (momentum confirmation)
# ─────────────────────────────────────────────────────────────────────────────
def compute_macd_hist_series(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """Returns MACD histogram Series: positive & rising = bullish momentum."""
    try:
        ema_fast   = df['Close'].ewm(span=fast,   adjust=False).mean()
        ema_slow   = df['Close'].ewm(span=slow,   adjust=False).mean()
        macd_line  = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return macd_line - signal_line
    except Exception as e:
        log.warning(f"MACD error: {e}")
        return pd.Series(0.0, index=df.index)


# ─────────────────────────────────────────────────────────────────────────────
#  OPENING RANGE BREAKOUT (ORB)  — 9:15–9:30 IST (first 3 bars of 5m chart)
#  Professional scalpers treat the break of this range as the day's first
#  high-probability directional bias signal.
# ─────────────────────────────────────────────────────────────────────────────
def compute_orb_series(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each bar, adds two columns:
      ORB_HIGH : first-30-min high of that bar's trading day
      ORB_LOW  : first-30-min low  of that bar's trading day
    Returns a copy of df with those columns added.
    """
    try:
        df = df.copy()
        df['_date'] = df.index.date

        orb_high = {}
        orb_low  = {}

        for date, day_df in df.groupby('_date'):
            # First 30 min = 9:15–9:44 IST (6 bars of 5-min)
            morning_bars = day_df.between_time('09:15', '09:44')
            if len(morning_bars) >= 2:
                orb_high[date] = morning_bars['High'].max()
                orb_low[date]  = morning_bars['Low'].min()
            else:
                orb_high[date] = day_df['High'].iloc[0]
                orb_low[date]  = day_df['Low'].iloc[0]

        df['ORB_HIGH'] = df['_date'].map(orb_high)
        df['ORB_LOW']  = df['_date'].map(orb_low)
        return df
    except Exception as e:
        log.warning(f"ORB error: {e}")
        df['ORB_HIGH'] = df['High']
        df['ORB_LOW']  = df['Low']
        return df


# ─────────────────────────────────────────────────────────────────────────────
#  MULTI-INDICATOR RATING SYSTEM
#  Modelled on TradingView Technical Ratings (normalised -1 to +1)
#  Adapted for 5-min Nifty options scalping.
#
#  OSCILLATORS  (max ±1.0 total):
#    RSI 14        : +1 if 55–75, -1 if 25–45, 0 otherwise
#    MACD histogram: +1 if positive & rising, -1 if negative & falling
#    Stoch-RSI     : +1 if <30, -1 if >70  (fade extremes for options timing)
#
#  MOVING AVERAGES  (max ±1.0 total):
#    EMA5 vs EMA13 : +1 bullish cross, -1 bearish cross
#    EMA9 vs price : +1 if price > EMA9, -1 if price < EMA9
#    SuperTrend 5m : +1 bullish, -1 bearish
#    VWAP          : +1 if price > VWAP, -1 if price < VWAP
#
#  STRUCTURE (max ±1.0 total — highest quality setups):
#    ORB breakout  : +1 if price broke and CLOSED above ORB high
#                   -1 if price broke and closed below ORB low
#    ADX strength  : multiplies the sum by (0.5 if ADX<20, 1.0 if 20-25, 1.2 if >25)
#
#  FINAL SCORE = avg(oscillators) + avg(moving_avgs) + structure
#  Range: -3.0 to +3.0  (can exceed slightly with ADX boost)
#
#  RATING:
#    score >= +0.7  → STRONG_BUY  → Buy CE with full size
#    score >= +0.3  → BUY         → Buy CE
#    score <= -0.7  → STRONG_SELL → Buy PE with full size
#    score <= -0.3  → SELL        → Buy PE
#    else           → NEUTRAL     → Skip
# ─────────────────────────────────────────────────────────────────────────────
def _rsi_signal(rsi: float) -> float:
    if 55 <= rsi <= 75:  return +1.0   # bullish momentum
    if 25 <= rsi <= 45:  return -1.0   # bearish momentum
    if rsi > 80:         return -0.5   # overbought — fade
    if rsi < 20:         return +0.5   # oversold — bounce
    return 0.0

def _stoch_rsi_signal(rsi_series: pd.Series, period: int = 14) -> float:
    """Stochastic RSI: measures RSI relative to its own min/max."""
    if len(rsi_series) < period + 1:
        return 0.0
    recent = rsi_series.iloc[-period:]
    rsi_now = rsi_series.iloc[-1]
    lo  = recent.min()
    hi  = recent.max()
    if hi == lo:
        return 0.0
    stoch_rsi = (rsi_now - lo) / (hi - lo) * 100
    if stoch_rsi < 30:  return +1.0   # oversold → buy signal
    if stoch_rsi > 70:  return -1.0   # overbought → sell signal
    return 0.0

def compute_multi_rating(
    df: pd.DataFrame,
    rsi_series: pd.Series,
    adx_val: float,
    macd_hist: pd.Series,
) -> dict:
    """
    Computes the multi-indicator composite rating at the LAST bar of df.

    Returns:
        {
            "score":     float  (-3 to +3),
            "rating":    str    (STRONG_BUY | BUY | NEUTRAL | SELL | STRONG_SELL),
            "direction": str    (CALL | PUT | NONE),
            "breakdown": dict   {indicator: value}
        }
    """
    try:
        close   = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2] if len(df) > 1 else close

        # ── Oscillators ─────────────────────────────────────────────────────
        rsi_val    = float(rsi_series.iloc[-1])
        rsi_sig    = _rsi_signal(rsi_val)
        stoch_sig  = _stoch_rsi_signal(rsi_series)
        macd_now   = float(macd_hist.iloc[-1])
        macd_prev  = float(macd_hist.iloc[-2]) if len(macd_hist) > 1 else macd_now
        if   macd_now > 0 and macd_now > macd_prev: macd_sig = +1.0
        elif macd_now < 0 and macd_now < macd_prev: macd_sig = -1.0
        else:                                        macd_sig =  0.0

        osc_score = (rsi_sig + stoch_sig + macd_sig) / 3.0  # normalised -1 to +1

        # ── Moving Averages ─────────────────────────────────────────────────
        ema5  = df['Close'].ewm(span=5,  adjust=False).mean().iloc[-1]
        ema9  = df['Close'].ewm(span=9,  adjust=False).mean().iloc[-1]
        ema13 = df['Close'].ewm(span=13, adjust=False).mean().iloc[-1]

        ema_cross_sig = +1.0 if ema5 > ema13 else -1.0
        ema9_sig      = +1.0 if close > ema9  else -1.0

        # VWAP (daily reset required — caller must ensure df has daily VWAP column)
        vwap_val  = float(df['VWAP'].iloc[-1]) if 'VWAP' in df.columns else close
        vwap_sig  = +1.0 if close > vwap_val * 1.001 else (-1.0 if close < vwap_val * 0.999 else 0.0)

        # SuperTrend
        st_dir    = float(df['ST_Dir'].iloc[-1]) if 'ST_Dir' in df.columns else 0
        st_sig    = -1.0 if st_dir == -1 else (+1.0 if st_dir == 1 else 0.0)
        # Note: ST_Dir convention: -1=bullish (price above), +1=bearish (price below)
        st_sig    = -st_sig   # flip: -1 direction means bullish for us

        ma_score  = (ema_cross_sig + ema9_sig + vwap_sig + st_sig) / 4.0   # -1 to +1

        # ── Structure (ORB) ─────────────────────────────────────────────────
        orb_high = float(df['ORB_HIGH'].iloc[-1]) if 'ORB_HIGH' in df.columns else close
        orb_low  = float(df['ORB_LOW'].iloc[-1])  if 'ORB_LOW'  in df.columns else close
        time_h   = df.index[-1].time().hour
        time_m   = df.index[-1].time().minute

        # ORB only valid after 9:45 IST (opening range has formed)
        orb_sig = 0.0
        if time_h > 9 or (time_h == 9 and time_m >= 45):
            if close > orb_high:    orb_sig = +1.0   # closed above ORB → bullish
            elif close < orb_low:   orb_sig = -1.0   # closed below ORB → bearish

        structure_score = orb_sig    # -1 to +1

        # ── ADX Strength Multiplier & Chopping Guard ───────────────────────
        if adx_val < 20:   adx_mult = 0.5     # choppy market — halve signals
        elif adx_val > 25: adx_mult = 1.2     # trending — slight boost
        else:              adx_mult = 1.0

        # ── Volume confirmation ─────────────────────────────────────────────
        vol_avg     = df['Volume'].rolling(20).mean().iloc[-1]
        vol_curr    = df['Volume'].iloc[-1]
        volume_ok   = bool(vol_curr > vol_avg * VOLUME_MULT_SCALP)
        vol_bonus   = 0.1 if volume_ok else 0.0

        # ── Final composite score ────────────────────────────────────────────
        raw_score = (osc_score * 0.8) + (ma_score * 1.0) + (structure_score * 1.2)
        score     = raw_score * adx_mult
        if score > 0: score += vol_bonus
        if score < 0: score -= vol_bonus

        # ── Rating assignment ────────────────────────────────────────────────
        # HARD RULE 1: ADX < 20 = choppy market → STRONG signals are BLOCKED
        #   Choppy markets cause false breakouts on options scalping.
        #   Max allowed rating in choppy market: BUY / SELL only.
        # HARD RULE 2: STRONG signals require volume confirmation (Vol:✅)
        #   Without volume, a breakout is likely a fake-out.
        choppy     = adx_val < 20
        no_volume  = not volume_ok

        if   score >= +0.7:
            if choppy or no_volume:
                rating = "BUY"         # downgrade STRONG_BUY if choppy or no volume
            else:
                rating = "STRONG_BUY"
        elif score >= +0.3: rating = "BUY"
        elif score <= -0.7:
            if choppy or no_volume:
                rating = "SELL"        # downgrade STRONG_SELL if choppy or no volume
            else:
                rating = "STRONG_SELL"
        elif score <= -0.3: rating = "SELL"
        else:               rating = "NEUTRAL"

        direction = "NONE"
        if rating in ("STRONG_BUY",  "BUY"):       direction = "CALL"
        if rating in ("STRONG_SELL", "SELL"):       direction = "PUT"

        return {
            "score":      round(score, 3),
            "rating":     rating,
            "direction":  direction,
            "breakdown": {
                "oscillator_score":  round(osc_score, 3),
                "ma_score":          round(ma_score, 3),
                "structure_orb":     orb_sig,
                "adx":               round(adx_val, 1),
                "adx_multiplier":    adx_mult,
                "choppy":            choppy,
                "rsi":               round(rsi_val, 1),
                "macd_hist":         round(macd_now, 4),
                "volume_confirm":    volume_ok,
            }
        }
    except Exception as e:
        log.error(f"Multi-rating error: {e}")
        return {"score": 0, "rating": "NEUTRAL", "direction": "NONE", "breakdown": {}}


# ─────────────────────────────────────────────────────────────────────────────
#  LEGACY: compute_scalp_signals  (kept for live scheduler compatibility)
#  Wraps the new multi-rating system.
# ─────────────────────────────────────────────────────────────────────────────
def compute_scalp_signals(df: pd.DataFrame) -> dict:
    """
    Main signal function for the live scheduler.
    Returns direction + confidence_score + full rating breakdown.
    """
    if df is None or len(df) < 50:
        return {"direction": "NO_SIGNAL", "confidence_score": 0}

    try:
        # Compute needed series
        rsi_delta  = df['Close'].diff()
        gain       = rsi_delta.where(rsi_delta > 0, 0).ewm(com=RSI_PERIOD - 1, adjust=False).mean()
        loss       = (-rsi_delta.where(rsi_delta < 0, 0)).ewm(com=RSI_PERIOD - 1, adjust=False).mean()
        rs         = gain / loss.replace(0, 1e-9)
        rsi_series = (100 - (100 / (1 + rs))).fillna(50)

        adx_val    = float(compute_adx_series(df).iloc[-1])
        macd_hist  = compute_macd_hist_series(df)

        # Add VWAP if not already present
        df = df.copy()
        if 'VWAP' not in df.columns:
            df['_date'] = df.index.date
            tp  = (df['High'] + df['Low'] + df['Close']) / 3
            df['VWAP'] = (tp * df['Volume']).groupby(df['_date']).cumsum() / \
                          df['Volume'].groupby(df['_date']).cumsum()

        # Add ST_Dir if not present
        if 'ST_Dir' not in df.columns:
            df['ST_Dir'] = compute_supertrend(df)

        # Add ORB if not present
        if 'ORB_HIGH' not in df.columns:
            df = compute_orb_series(df)

        rating = compute_multi_rating(df, rsi_series, adx_val, macd_hist)

        direction_map = {
            "STRONG_BUY":  "SCALP_LONG",
            "BUY":         "SCALP_LONG",
            "STRONG_SELL": "SCALP_SHORT",
            "SELL":        "SCALP_SHORT",
            "NEUTRAL":     "NO_SIGNAL",
        }

        return {
            "direction":       direction_map[rating["rating"]],
            "confidence_score": abs(rating["score"]),
            "rating":          rating["rating"],
            "score":           rating["score"],
            "breakdown":       rating["breakdown"],
        }

    except Exception as e:
        log.error(f"compute_scalp_signals error: {e}")
        return {"direction": "NO_SIGNAL", "confidence_score": 0}


# ─────────────────────────────────────────────────────────────────────────────
#  KEY LEVELS  (pre-market LLM context — unchanged)
# ─────────────────────────────────────────────────────────────────────────────
def compute_key_levels(df: pd.DataFrame) -> str:
    if df is None or df.empty or len(df) < 200:
        return "{}"
    try:
        import json
        current_price = df['Close'].iloc[-1]
        high_52 = df['High'].rolling(252).max().iloc[-1] if len(df) >= 252 else df['High'].max()
        low_52  = df['Low'].rolling(252).min().iloc[-1]  if len(df) >= 252 else df['Low'].min()
        ema50   = df['Close'].ewm(span=50,  adjust=False).mean().iloc[-1]
        ema200  = df['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
        trend   = "BULLISH" if ema50 > ema200 else "BEARISH"
        levels  = {
            "current_price":   float(current_price),
            "52_week_high":    float(high_52),
            "52_week_low":     float(low_52),
            "macro_trend_ema": trend,
            "ema_50":          float(ema50),
            "ema_200":         float(ema200),
            "atr_14":          compute_atr(df, 14),
        }
        return json.dumps(levels)
    except Exception as e:
        log.warning(f"compute_key_levels error: {e}")
        return "{}"
