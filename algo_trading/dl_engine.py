import pandas as pd
import numpy as np
from .logger import log

class DLEnginePlaceholder:
    """
    Placeholder for the actual Deep Learning (Transformer/LSTM) model.
    In the future, this class will load a .tflite or scikit-learn model and
    run true inference. For now, it simulates the DL output so the pipeline
    can be built and tested.
    """
    def __init__(self, model_path: str = "models/intraday_transformer.tflite"):
        self.model_path = model_path
        self.is_loaded = False
        self._load_model()

    def _load_model(self):
        # TODO: Implement actual model loading here (e.g., tflite_runtime.Interpreter)
        # log.info(f"Loaded Intraday DL Model from {self.model_path}")
        self.is_loaded = True

    def predict(self, df: pd.DataFrame) -> dict:
        """
        Runs inference on the latest N candles.
        Returns probabilities (0 to 1) for Direction, Volume Surge, and Volatility Expansion.
        """
        if df.empty or len(df) < 20:
            return {"direction_bull": 0.5, "direction_bear": 0.5, "vol_surge_prob": 0.0, "volatility_prob": 0.0}

        # --- SIMULATED DL INFERENCE ---
        # We simulate the AI's predictions using recent price action so the backtester has data to work with.
        
        close = df['Close'].iloc[-1]
        sma20 = df['Close'].rolling(20).mean().iloc[-1]
        
        # 1. Direction Probability
        bull_prob = 0.8 if close > sma20 * 1.002 else 0.4
        bear_prob = 0.8 if close < sma20 * 0.998 else 0.4

        # 2. Volume Surge Probability
        vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
        vol_now = df['Volume'].iloc[-1]
        vol_surge_prob = 0.9 if vol_now > vol_avg * 1.2 else 0.3

        # 3. Volatility Expansion Probability (Vega)
        atr_14 = (df['High'] - df['Low']).rolling(14).mean().iloc[-1]
        recent_range = df['High'].iloc[-1] - df['Low'].iloc[-1]
        volatility_prob = 0.85 if recent_range > atr_14 * 1.1 else 0.4

        return {
            "direction_bull": bull_prob,
            "direction_bear": bear_prob,
            "vol_surge_prob": vol_surge_prob,
            "volatility_prob": volatility_prob
        }

# Singleton instance to avoid reloading weights repeatedly
dl_model = DLEnginePlaceholder()

def compute_dl_rating(df: pd.DataFrame) -> dict:
    """
    Called by the intraday_scheduler and backtest_intraday.
    Evaluates the DL model and returns a composite Intraday Rating.
    """
    try:
        preds = dl_model.predict(df)
        
        bull_p = preds["direction_bull"]
        bear_p = preds["direction_bear"]
        vol_p  = preds["vol_surge_prob"]
        vega_p = preds["volatility_prob"]

        # Intraday trades require CONFIDENCE in both direction AND expansion.
        # Options buying decays over hours, so we MUST have high Volatility/Volume probability.
        
        rating = "NEUTRAL"
        direction = "NONE"
        score = 0.0

        if bull_p > 0.70 and vol_p > 0.60 and vega_p > 0.60:
            rating = "STRONG_BUY"
            direction = "CALL"
            score = bull_p * 100

        elif bear_p > 0.70 and vol_p > 0.60 and vega_p > 0.60:
            rating = "STRONG_SELL"
            direction = "PUT"
            score = bear_p * -100

        return {
            "score": score,
            "rating": rating,
            "direction": direction,
            "breakdown": {
                "dl_bull_prob": round(bull_p, 2),
                "dl_bear_prob": round(bear_p, 2),
                "dl_vol_prob":  round(vol_p, 2),
                "dl_vega_prob": round(vega_p, 2)
            }
        }
    except Exception as e:
        log.error(f"DL Rating Error: {e}")
        return {"score": 0, "rating": "NEUTRAL", "direction": "NONE", "breakdown": {}}
