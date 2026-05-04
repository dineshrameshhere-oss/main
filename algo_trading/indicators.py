import pandas as pd
import numpy as np
import math
from .logger import log
from .config import (
    SUPERTREND_PERIOD, SUPERTREND_MULT,
    EMA_FAST, EMA_SLOW, RSI_PERIOD,
    VOLUME_MULT_SCALP, RSI_OVERBOUGHT, RSI_OVERSOLD,
    CANDLE_MIN_BODY_RATIO, CANDLE_REJECTION_WICK,
    MOMENTUM_BARS, MOMENTUM_MIN_MOVE_PCT,
    MIN_NIFTY_HOURLY_RANGE_PCT,
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

# ─────────────────────────────────────────────────────────────────────────────
#  CANDLE QUALITY — false breakout filter
# ─────────────────────────────────────────────────────────────────────────────
def _candle_quality(df: pd.DataFrame, direction: str) -> dict:
    """
    Analyses the last bar for doji / rejection candle patterns.

    Returns:
        {
          'is_rejection': bool  — True = skip this entry,
          'body_ratio':   float — body / total range (0–1),
          'reason':       str
        }

    A rejection candle is one of:
      • Doji: body < 30% of total range (indecision)
      • Bearish rejection on bullish signal: upper wick > 2× body (price tried up, got slapped)
      • Bullish rejection on bearish signal: lower wick > 2× body (price tried down, got bid up)
    """
    try:
        row   = df.iloc[-1]
        open_ = float(row['Open'])
        high  = float(row['High'])
        low   = float(row['Low'])
        close = float(row['Close'])

        total_range = high - low
        if total_range < 0.01:
            return {'is_rejection': False, 'body_ratio': 1.0, 'reason': 'flat_bar'}

        body        = abs(close - open_)
        upper_wick  = high - max(open_, close)
        lower_wick  = min(open_, close) - low
        body_ratio  = body / total_range

        # Doji — total indecision regardless of direction
        if body_ratio < CANDLE_MIN_BODY_RATIO:
            return {'is_rejection': True, 'body_ratio': round(body_ratio, 2),
                    'reason': f'doji (body={body_ratio:.0%} < {CANDLE_MIN_BODY_RATIO:.0%})'}

        # Rejection wick on bullish signal (bearish pin bar)
        if direction == 'SCALP_LONG' and body > 0:
            if upper_wick > CANDLE_REJECTION_WICK * body:
                return {'is_rejection': True, 'body_ratio': round(body_ratio, 2),
                        'reason': f'bearish_rejection_wick (upper={upper_wick:.1f} > {CANDLE_REJECTION_WICK}×body={body:.1f})'}

        # Rejection wick on bearish signal (bullish pin bar)
        if direction == 'SCALP_SHORT' and body > 0:
            if lower_wick > CANDLE_REJECTION_WICK * body:
                return {'is_rejection': True, 'body_ratio': round(body_ratio, 2),
                        'reason': f'bullish_rejection_wick (lower={lower_wick:.1f} > {CANDLE_REJECTION_WICK}×body={body:.1f})'}

        return {'is_rejection': False, 'body_ratio': round(body_ratio, 2), 'reason': 'ok'}
    except Exception as e:
        return {'is_rejection': False, 'body_ratio': 0.5, 'reason': f'error:{e}'}


# ─────────────────────────────────────────────────────────────────────────────
#  MOMENTUM FILTER — require directional move before entry
# ─────────────────────────────────────────────────────────────────────────────
def _momentum_signal(df: pd.DataFrame, direction: str) -> dict:
    """
    Returns whether the last MOMENTUM_BARS bars show directional momentum.
    Uses % move relative to current price (scale-invariant).

    For SCALP_LONG:  (Close[-1] - Close[-MOMENTUM_BARS]) / Close[-MOMENTUM_BARS] > PCT
    For SCALP_SHORT: (Close[-MOMENTUM_BARS] - Close[-1])  / Close[-MOMENTUM_BARS] > PCT
    """
    try:
        if len(df) < MOMENTUM_BARS + 1:
            return {'ok': True, 'move_pct': 0, 'required_pct': MOMENTUM_MIN_MOVE_PCT}

        close_now  = float(df['Close'].iloc[-1])
        close_back = float(df['Close'].iloc[-MOMENTUM_BARS])
        ref        = max(close_back, 1e-6)

        if direction == 'SCALP_LONG':
            move_pct = (close_now - close_back) / ref
        else:
            move_pct = (close_back - close_now) / ref

        return {
            'ok':           move_pct >= MOMENTUM_MIN_MOVE_PCT,
            'move_pct':     round(move_pct * 100, 3),
            'required_pct': MOMENTUM_MIN_MOVE_PCT * 100,
        }
    except Exception as e:
        return {'ok': True, 'move_pct': 0, 'required_pct': MOMENTUM_MIN_MOVE_PCT * 100}


# ─────────────────────────────────────────────────────────────────────────────
#  REALIZED VOLATILITY GATE — skip flat days
# ─────────────────────────────────────────────────────────────────────────────
def check_rv_gate(df: pd.DataFrame) -> dict:
    """
    Checks if today's Nifty range (High-Low over last 12 bars = 1 hour)
    exceeds MIN_NIFTY_HOURLY_RANGE_PCT of current price.
    Scale-invariant: works on both scaled (~1107) and real (~24000) price data.
    """
    try:
        look    = min(12, len(df))    # 12 × 5m = 60 min
        recent  = df.iloc[-look:]
        current_price  = float(df['Close'].iloc[-1])
        hourly_range   = float(recent['High'].max() - recent['Low'].min())
        range_pct      = hourly_range / max(current_price, 1e-6)
        required_pct   = MIN_NIFTY_HOURLY_RANGE_PCT
        return {
            'ok':        range_pct >= required_pct,
            'range_pts': round(hourly_range, 2),
            'range_pct': round(range_pct * 100, 3),
            'required':  required_pct * 100,
        }
    except Exception as e:
        return {'ok': True, 'range_pts': 0, 'range_pct': 0, 'required': 0}


def _rsi_signal(rsi: float) -> float:
    if 60 <= rsi <= 80:  return +1.0   # bullish momentum
    if 20 <= rsi <= 40:  return -1.0   # bearish momentum
    if rsi > 85:         return -0.5   # overbought — fade
    if rsi < 15:         return +0.5   # oversold — bounce
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

# ─────────────────────────────────────────────────────────────────────────────
#  MULTI-TIMEFRAME (MTF) TREND
# ─────────────────────────────────────────────────────────────────────────────
def compute_mtf_trend_score(df_3m: pd.DataFrame, df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> float:
    """
    Computes a trend confirmation score across 3m, 5m, and 15m timeframes.
    Each timeframe contributes to the score based on EMA and SuperTrend alignment.
    
    Returns: -1.0 to +1.0 (Full Bearish to Full Bullish)
    """
    def get_tf_signal(df):
        if df is None or df.empty: return 0.0
        try:
            close = df['Close'].iloc[-1]
            ema9  = df['Close'].ewm(span=9, adjust=False).mean().iloc[-1]
            st_dir = compute_supertrend(df).iloc[-1] # -1 bullish, +1 bearish
            
            sig = 0.0
            if close > ema9: sig += 0.5
            else: sig -= 0.5
            
            if st_dir == -1: sig += 0.5
            else: sig -= 0.5
            return sig
        except: return 0.0

    s3  = get_tf_signal(df_3m)  # weight 0.2
    s5  = get_tf_signal(df_5m)  # weight 0.3
    s15 = get_tf_signal(df_15m) # weight 0.5 (highest weight for HTF)
    
    return (s3 * 0.2) + (s5 * 0.3) + (s15 * 0.5)

def compute_multi_rating(
    df: pd.DataFrame,
    rsi_series: pd.Series,
    adx_val: float,
    macd_hist: pd.Series,
    pcr: dict | None = None,
    fnf_direction: float = 0.0,
    ivr_signal: float = 0.0,
    score_history: list | None = None,
    mtf_score: float = 0.0,
) -> dict:
    """
    Proven composite scoring (6 components) with upgrades:
      + MTF Trend Confirmation [−1.0..+1.0] — weight 1.2 (HIGH CONVICTION)
      + Consensus multiplier [0.88..1.00] — suppresses signals where most indicators
        disagree strongly (anti-false-signal for leading indicators)
      + FinNifty addend [−0.20..+0.20] — financial sector confirmation

    Core components:
      1. Oscillators  (RSI + StochRSI + MACD)   weight 0.7
      2. Moving Avgs  (EMA5/13 + EMA9 + VWAP + SuperTrend) weight 0.9
      3. Structure    (ORB breakout)              weight 1.0
      4. OI Coverage  (4-type smart-money flow)  weight 1.1
      5. PCR          (options sentiment)         weight 0.4
      6. MTF Trend    (3m, 5m, 15m alignment)     weight 1.2

    Consensus (anti-false-signal layer):
      Counts leading indicators (Stoch-K, WR, ROC, Volume, Candle) that strongly
      agree or oppose the direction. If most oppose → consensus_mult < 1.0 → score
      reduced. Range: [0.88, 1.00] — mild enough to keep trade frequency, firm
      enough to kill noise-only spikes.

    ADX: mild multiplier [0.5..1.2] — choppy market dampens, trending boosts.
    STRONG_BUY/SELL threshold: ±0.7 (same as original proven system).
    """
    try:
        close      = float(df["Close"].iloc[-1])
        open_now   = float(df["Open"].iloc[-1])
        prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else close

        # ── Oscillators ───────────────────────────────────────────────────────
        rsi_val    = float(rsi_series.iloc[-1])
        rsi_sig    = _rsi_signal(rsi_val)
        stoch_sig  = _stoch_rsi_signal(rsi_series)
        macd_now   = float(macd_hist.iloc[-1])
        macd_prev  = float(macd_hist.iloc[-2]) if len(macd_hist) > 1 else macd_now
        if   macd_now > 0 and macd_now > macd_prev: macd_sig = +1.0
        elif macd_now < 0 and macd_now < macd_prev: macd_sig = -1.0
        else:                                        macd_sig =  0.0
        osc_score = (rsi_sig + stoch_sig + macd_sig) / 3.0

        # ── Moving Averages ───────────────────────────────────────────────────
        ema5  = df["Close"].ewm(span=5,  adjust=False).mean().iloc[-1]
        ema9  = df["Close"].ewm(span=9,  adjust=False).mean().iloc[-1]
        ema13 = df["Close"].ewm(span=13, adjust=False).mean().iloc[-1]
        ema_cross_sig = +1.0 if ema5 > ema13 else -1.0
        ema9_sig      = +1.0 if close > ema9  else -1.0
        vwap_val  = float(df["VWAP"].iloc[-1]) if "VWAP" in df.columns else close
        vwap_sig  = +1.0 if close > vwap_val * 1.001 else (-1.0 if close < vwap_val * 0.999 else 0.0)
        st_dir    = float(df["ST_Dir"].iloc[-1]) if "ST_Dir" in df.columns else 0
        st_sig    = -st_dir  # st_dir: -1=bullish, +1=bearish -> st_sig: +1=bullish, -1=bearish
        ma_score  = (ema_cross_sig + ema9_sig + vwap_sig + st_sig) / 4.0

        # ── Structure (ORB) ───────────────────────────────────────────────────
        orb_high = float(df["ORB_HIGH"].iloc[-1]) if "ORB_HIGH" in df.columns else close
        orb_low  = float(df["ORB_LOW"].iloc[-1])  if "ORB_LOW"  in df.columns else close
        t = df.index[-1].time()
        orb_sig = 0.0
        if t.hour > 9 or (t.hour == 9 and t.minute >= 45):
            if   close > orb_high: orb_sig = +1.0
            elif close < orb_low:  orb_sig = -1.0
        structure_score = orb_sig

        # ── OI Coverage ───────────────────────────────────────────────────────
        pmove  = close - (float(df["Close"].iloc[-6]) if len(df) >= 6 else close)
        vnow   = float(df["Volume"].iloc[-1])
        vma6   = float(df["Volume"].iloc[-6:].mean()) if len(df) >= 6 else vnow
        vol_up = vnow > vma6 * 1.05
        if   pmove > 0 and vol_up:  oi_type = "LONG_BUILDUP";   oi_score = +1.0
        elif pmove < 0 and vol_up:  oi_type = "SHORT_BUILDUP";  oi_score = -1.0
        elif pmove > 0:             oi_type = "SHORT_COVERING"; oi_score = +0.4
        elif pmove < 0:             oi_type = "LONG_UNWINDING"; oi_score = -0.4
        else:                       oi_type = "NEUTRAL";        oi_score =  0.0

        # ── Volume confirmation ───────────────────────────────────────────────
        vol_avg  = df["Volume"].rolling(20).mean().iloc[-1]
        vol_curr = float(df["Volume"].iloc[-1])
        vol_ratio = vol_curr / max(vol_avg, 1.0)
        volume_ok = vol_ratio >= VOLUME_MULT_SCALP
        vol_bonus = 0.1 if volume_ok else 0.0

        # ── PCR ───────────────────────────────────────────────────────────────
        pcr_val   = float(pcr.get("pcr", 1.0)) if pcr else 1.0
        pcr_score = max(-0.5, min(0.5, (1.0 - pcr_val) * 0.5))

        # ── Core composite score (proven weights) ─────────────────────────────
        raw_score = (
            osc_score       * 0.7 +
            ma_score        * 0.9 +
            structure_score * 1.0 +
            oi_score        * 1.1 +
            pcr_score       * 0.4 +
            mtf_score       * 1.2
        )

        # ── ADX multiplier ────────────────────────────────────────────────────
        if   adx_val < 20:   adx_mult = 0.5
        elif adx_val > 25:   adx_mult = 1.2
        else:                adx_mult = 1.0

        score = raw_score * adx_mult
        if score > 0: score += vol_bonus
        if score < 0: score -= vol_bonus

        # ── CONSENSUS ANTI-FALSE-SIGNAL LAYER (mild, 0.88–1.00) ──────────────
        # Computes fast-leading indicators independently as agreement voters.
        # If most of these STRONGLY oppose the intended direction, it likely
        # means the core EMA/MACD signal is a false start — gently reduce score.
        # Range: [0.88, 1.00] — at most 12% reduction, preserving trade frequency.
        k   = min(5, len(df))
        hk  = df["High"].rolling(k).max().iloc[-1]
        lk  = df["Low"].rolling(k).min().iloc[-1]
        stk = ((close - lk) / max(hk - lk, 1e-6)) * 100
        wr  = min(14, len(df))
        hw  = df["High"].rolling(wr).max().iloc[-1]
        lw  = df["Low"].rolling(wr).min().iloc[-1]
        wlr = ((hw - close) / max(hw - lw, 1e-6)) * -100

        close_back = float(df["Close"].iloc[-min(6, len(df))])
        roc = (close - close_back) / max(abs(close_back), 1e-6)

        crange  = float(df["High"].iloc[-1]) - float(df["Low"].iloc[-1])
        cbody   = abs(close - open_now)
        bratio  = cbody / max(crange, 1e-6)
        cdir    = +1.0 if close >= open_now else -1.0
        candle_v = bratio * cdir   # positive = bullish body, negative = bearish

        stk_v = +1.0 if stk <= 20 else (-1.0 if stk >= 80 else 0.0)
        wlr_v = +1.0 if wlr <= -80 else (-1.0 if wlr >= -20 else 0.0)
        roc_v = +1.0 if roc > 0.001 else (-1.0 if roc < -0.001 else 0.0)
        vol_v = +1.0 if (vol_ratio >= 1.5 and close > prev_close) else (
                -1.0 if (vol_ratio >= 1.5 and close < prev_close) else 0.0)

        intended = +1 if score >= 0 else -1
        voters   = [stk_v, wlr_v, roc_v, vol_v, candle_v]
        agree    = sum(1 for v in voters if v * intended >  0.5)
        oppose   = sum(1 for v in voters if v * intended < -0.5)
        # mild: only penalise when MOST strong voters oppose
        if oppose > agree and oppose >= 3:
            consensus_mult = 0.88   # majority opposes → reduce 12%
        elif oppose > agree:
            consensus_mult = 0.94   # slight majority opposes → reduce 6%
        else:
            consensus_mult = 1.00   # neutral or agreeing → no change

        score = score * consensus_mult

        # ── FinNifty addend (small directional boost/penalty) ─────────────────
        fnf_addend = float(fnf_direction) * 0.20
        score = score + fnf_addend

        # ── IVR addend (IV Rank signal — safe default 0.0 if unavailable) ───
        # +0.10: IVR<25 — premium cheap, expansion likely  → small boost
        # -0.15: IVR>75 — premium expensive, crush risk   → small penalty
        #  0.00: IVR 25-75 or API unavailable             → no effect
        score = score + float(ivr_signal)

        # ── RSI DIRECTIONAL FILTER (Tightened) ─────────────────────────────
        # If we are BUYING (Long), RSI must be above 50 (bullish zone).
        # If we are SELLING (Short), RSI must be below 50 (bearish zone).
        # This prevents entering a PE during a small dip in an uptrend.
        if score > 0 and rsi_val < 50:
            score *= 0.5  # Penalize bullish score if RSI < 50
        elif score < 0 and rsi_val > 50:
            score *= 0.5  # Penalize bearish score if RSI > 50

        # ── TREND CONSISTENCY (Cooldown / Anti-Whipsaw) ──────────────────────
        # Check if we are reversing direction too fast.
        # If we had a STRONG SELL in the last 15 mins and now have a STRONG BUY,
        # it might be a volatility trap.
        # We require at least 2 consecutive bars of consistent bias for "STRONG" signals.
        if score_history and len(score_history) >= 2:
            s1, s2 = score_history[-1], score_history[-2]
            # If reversing (sign change) AND previous was strong, penalize current
            if (score * s1 < 0) and (abs(s1) >= 0.5):
                score *= 0.7  # reduce conviction on immediate reversal
        
        # ── Rating assignment ─────────────────────────────────────────────────
        choppy    = adx_val < 20
        no_volume = not volume_ok

        if   score >= +0.7:
            rating = "STRONG_BUY"  if not (choppy or no_volume) else "BUY"
        elif score >= +0.3: rating = "BUY"
        elif score <= -0.7:
            rating = "STRONG_SELL" if not (choppy or no_volume) else "SELL"
        elif score <= -0.3: rating = "SELL"
        else:               rating = "NEUTRAL"

        direction = "NONE"
        if rating in ("STRONG_BUY",  "BUY"):  direction = "CALL"
        if rating in ("STRONG_SELL", "SELL"): direction = "PUT"

        return {
            "score":     round(score, 3),
            "rating":    rating,
            "direction": direction,
            "breakdown": {
                "oscillator_score": round(osc_score, 3),
                "ma_score":         round(ma_score, 3),
                "oi_coverage":      oi_type,
                "oi_score":         round(oi_score, 1),
                "pcr_val":          round(pcr_val, 3),
                "pcr_score":        round(pcr_score, 3),
                "structure_orb":    orb_sig,
                "adx":              round(adx_val, 1),
                "adx_multiplier":   adx_mult,
                "choppy":           choppy,
                "rsi":              round(rsi_val, 1),
                "stoch_k":          round(stk, 1),
                "willr":            round(wlr, 1),
                "consensus_mult":   consensus_mult,
                "agree_voters":     agree,
                "oppose_voters":    oppose,
                "finnifty_dir":     fnf_direction,
                "ivr_signal":       round(ivr_signal, 2),
                "volume_confirm":   volume_ok,
            },
        }
    except Exception as e:
        log.error(f"Multi-rating error: {e}")
        return {"score": 0, "rating": "NEUTRAL", "direction": "NONE", "breakdown": {}}






# ─────────────────────────────────────────────────────────────────────────────
#  OPTION GREEKS  (Black-Scholes, pure math — no scipy dependency)
# ─────────────────────────────────────────────────────────────────────────────
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def compute_greeks(spot: float, strike: float, days_to_expiry: int,
                   premium: float, opt_type: str = 'CE',
                   risk_free: float = 0.065) -> dict:
    """
    Black-Scholes option greeks. IV estimated via Brenner-Subrahmanyam (1988).

    Args:
        spot            : Nifty spot price
        strike          : Option strike price
        days_to_expiry  : Calendar days to expiry
        premium         : Current option premium (for IV estimation)
        opt_type        : 'CE' or 'PE'
        risk_free       : India risk-free rate (RBI repo ~6.5%)

    Returns delta (abs), gamma, theta (daily decay ₹/unit), vega, iv_pct.
    """
    try:
        # ── DYNAMIC SPOT HANDLING ───────────────────────────────────────────
        # NSE_3045 returns ~1107 (scaled). Real Nifty is ~24000.
        # If spot is scaled, we must un-scale it for Black-Scholes.
        # Ratio is approx 24000 / 1107 ≈ 21.68
        if spot < 5000:
             # Inferred scale: if spot=1107 and strike=24100, ratio=21.77
             # We use the strike price as the anchor since it's always real.
             scale_factor = strike / spot
             log.debug(f"Greeks: Scaling spot {spot} by {scale_factor:.2f} to match strike {strike}")
             spot = spot * scale_factor

        T = max(days_to_expiry, 0.5) / 365.0   # min 12hr to avoid div/0

        # Implied Volatility: Brenner-Subrahmanyam approximation
        iv = (premium / max(spot, 1)) * math.sqrt(2 * math.pi / T)
        iv = max(0.05, min(iv, 3.0))   # clamp 5%–300%

        d1 = (math.log(spot / max(strike, 1)) + (risk_free + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)

        nd1 = _norm_cdf(d1);  nd2 = _norm_cdf(d2)
        pd1 = _norm_pdf(d1)

        delta = nd1 if opt_type == 'CE' else nd1 - 1.0
        gamma = pd1 / max(spot * iv * math.sqrt(T), 1e-9)
        theta = (-(spot * pd1 * iv) / (2 * math.sqrt(T))
                 - risk_free * strike * math.exp(-risk_free * T)
                 * (nd2 if opt_type == 'CE' else -_norm_cdf(-d2))) / 365.0
        vega  = spot * pd1 * math.sqrt(T) / 100.0  # ₹ per 1% IV change

        return {
            'delta':  round(abs(delta), 3),
            'gamma':  round(gamma, 5),
            'theta':  round(theta, 2),     # negative = daily decay ₹
            'vega':   round(vega, 2),
            'iv_pct': round(iv * 100, 1),
        }
    except Exception:
        return {'delta': 0.3, 'gamma': 0.001, 'theta': -5.0, 'vega': 1.0, 'iv_pct': 20.0}


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

        # MTF Trend fallback (using 5m as base for all if not provided)
        mtf_score = compute_mtf_trend_score(df, df, df)

        rating = compute_multi_rating(df, rsi_series, adx_val, macd_hist, mtf_score=mtf_score)

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
