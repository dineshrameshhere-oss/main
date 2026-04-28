import pandas as pd
import numpy as np
from .logger import log
from .config import (
    SUPERTREND_PERIOD, SUPERTREND_MULT, 
    EMA_FAST, EMA_SLOW, RSI_PERIOD,
    VOLUME_MULT_SCALP, RSI_OVERBOUGHT, RSI_OVERSOLD
)

def compute_key_levels(df: pd.DataFrame) -> str:
    """
    Computes key support/resistance and trend levels for pre-market LLM context.
    """
    if df is None or df.empty or len(df) < 200:
        return "{}"
        
    try:
        current_price = df['Close'].iloc[-1]
        
        high_52 = df['High'].rolling(252).max().iloc[-1] if len(df) >= 252 else df['High'].max()
        low_52 = df['Low'].rolling(252).min().iloc[-1] if len(df) >= 252 else df['Low'].min()
        
        ema50 = df['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = df['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
        
        trend = "BULLISH" if ema50 > ema200 else "BEARISH"
        
        levels = {
            "current_price": float(current_price),
            "52_week_high": float(high_52),
            "52_week_low": float(low_52),
            "macro_trend_ema": trend,
            "ema_50": float(ema50),
            "ema_200": float(ema200)
        }
        import json
        return json.dumps(levels)
    except Exception as e:
        log.warning(f"⚠️ Error computing key levels: {e}")
        return "{}"

def compute_scalp_signals(df: pd.DataFrame) -> dict:
    """
    Evaluates the 3-minute chart for scalp entries using pure pandas.
    """
    if df is None or len(df) < EMA_SLOW:
        return {"direction": "NO_SIGNAL", "confidence_score": 0}

    try:
        ema_f = df['Close'].ewm(span=EMA_FAST, adjust=False).mean().iloc[-1]
        ema_s = df['Close'].ewm(span=EMA_SLOW, adjust=False).mean().iloc[-1]
        
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=RSI_PERIOD).mean().iloc[-1]
        loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_PERIOD).mean().iloc[-1]
        rs = gain / loss if loss != 0 else 0
        rsi = 100 - (100 / (1 + rs)) if rs != 0 else 50
        
        tp = (df['High'] + df['Low'] + df['Close']) / 3
        vwap = (tp * df['Volume']).cumsum() / df['Volume'].cumsum()
        vwap_val = vwap.iloc[-1]

        vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
        vol_curr = df['Volume'].iloc[-1]
        
        current_price = df['Close'].iloc[-1]
        
        # ── SCALP LOGIC (Scoring System to prevent over-filtering) ──
        long_score = 0
        short_score = 0
        
        # Factor 1: Trend (EMA)
        if ema_f > ema_s: long_score += 1
        elif ema_f < ema_s: short_score += 1
            
        # Factor 2: Intraday Value (VWAP)
        if current_price > vwap_val: long_score += 1
        elif current_price < vwap_val: short_score += 1
            
        # Factor 3: Momentum (RSI)
        if 55 < rsi < RSI_OVERBOUGHT: long_score += 1
        elif 45 > rsi > RSI_OVERSOLD: short_score += 1
            
        # Bonus Factor: Volume Surge
        vol_confirm = vol_curr > (vol_avg * VOLUME_MULT_SCALP)
        if vol_confirm:
            if long_score > 0: long_score += 0.5
            if short_score > 0: short_score += 0.5
            
        # PROFITABILITY FILTER (Discovered via Backtest)
        # Macro trend alignment completely eliminates losing chop trades
        ema200 = df['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
        if current_price < ema200:
            long_score = 0
        if current_price > ema200:
            short_score = 0
            
        direction = "NO_SIGNAL"
        final_score = 0
        
        # Require at least 2 points (e.g., EMA + VWAP, or VWAP + RSI)
        if long_score >= 2.0:
            direction = "SCALP_LONG"
            final_score = long_score
        elif short_score >= 2.0:
            direction = "SCALP_SHORT"
            final_score = short_score
            
        return {
            "direction": direction,
            "tier1_pass": bool(long_score >= 1 or short_score >= 1),
            "tier2_pass": True,
            "volume_confirm": bool(vol_confirm),
            "confidence_score": final_score
        }

    except Exception as e:
        log.error(f"❌ Error computing scalp signals: {e}")
        return {"direction": "NO_SIGNAL", "confidence_score": 0}
