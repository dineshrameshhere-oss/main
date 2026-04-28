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
        
        # Simplified directional filter using EMA crossover instead of SuperTrend
        st_dir = 1 if ema_f > ema_s else -1
        
        # ── SCALP LOGIC ──
        tier1_long = (st_dir == 1) and (current_price > vwap_val)
        tier1_short = (st_dir == -1) and (current_price < vwap_val)
        
        tier2_long = (ema_f > ema_s) or (rsi < RSI_OVERBOUGHT)
        tier2_short = (ema_f < ema_s) or (rsi > RSI_OVERSOLD)
        
        vol_confirm = vol_curr > (vol_avg * VOLUME_MULT_SCALP)
        
        direction = "NO_SIGNAL"
        score = 0
        
        if tier1_long and tier2_long:
            direction = "SCALP_LONG"
            score = 2 + (1 if vol_confirm else 0)
        elif tier1_short and tier2_short:
            direction = "SCALP_SHORT"
            score = 2 + (1 if vol_confirm else 0)
            
        return {
            "direction": direction,
            "tier1_pass": bool(tier1_long or tier1_short),
            "tier2_pass": bool(tier2_long or tier2_short),
            "volume_confirm": bool(vol_confirm),
            "confidence_score": score
        }

    except Exception as e:
        log.error(f"❌ Error computing scalp signals: {e}")
        return {"direction": "NO_SIGNAL", "confidence_score": 0}
