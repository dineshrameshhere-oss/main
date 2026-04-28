"""
DL Engine — Real ML Inference
==============================
Loads pre-trained scikit-learn models from models/ directory.
Replaces the placeholder SMA-based dummy logic entirely.

Models required (run python -m algo_trading.ml_trainer to generate):
  models/direction_model.pkl  — RandomForest predicts UP(+1)/NEUTRAL(0)/DOWN(-1)
  models/vol_model.pkl        — GradientBoosting predicts Volume Surge YES(1)/NO(0)
  models/vega_model.pkl       — GradientBoosting predicts Vega Expansion YES(1)/NO(0)
  models/feature_cols.pkl     — list of feature column names used during training

All features are scale-invariant (% returns, ratios, bounded oscillators).
"""
import pathlib
import pickle
import numpy as np
import pandas as pd

from .logger import log

MODELS_DIR = pathlib.Path(__file__).parent.parent / "models"

# ── Model Loading (once at import, not on every poll) ─────────────────────────
_dir_model   = None
_vol_model   = None
_vega_model  = None
_feature_cols = None


def _load_models():
    global _dir_model, _vol_model, _vega_model, _feature_cols
    required = [
        MODELS_DIR / "direction_model.pkl",
        MODELS_DIR / "vol_model.pkl",
        MODELS_DIR / "vega_model.pkl",
        MODELS_DIR / "feature_cols.pkl",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"\n❌ ML models not found. Train them first:\n"
            f"   python -m algo_trading.ml_trainer\n\n"
            f"Missing: {missing}"
        )
    with open(MODELS_DIR / "direction_model.pkl", "rb") as f:
        _dir_model = pickle.load(f)
    with open(MODELS_DIR / "vol_model.pkl", "rb") as f:
        _vol_model = pickle.load(f)
    with open(MODELS_DIR / "vega_model.pkl", "rb") as f:
        _vega_model = pickle.load(f)
    with open(MODELS_DIR / "feature_cols.pkl", "rb") as f:
        _feature_cols = pickle.load(f)
    log.info(f"✅ ML models loaded from {MODELS_DIR} | {len(_feature_cols)} features")


try:
    _load_models()
except FileNotFoundError as e:
    log.warning(str(e))


# ── Feature Builder (must match ml_trainer.py exactly) ───────────────────────
LAG_BARS = 10   # must match ml_trainer.LAG_BARS


def _build_live_features(df: pd.DataFrame) -> pd.DataFrame | None:
    """
    Given a OHLCV DataFrame of the last N 15m bars, builds the same feature
    vector that was used during training. Returns a single-row DataFrame.
    """
    if len(df) < LAG_BARS + 15:
        log.warning(f"Not enough bars for ML inference: need {LAG_BARS + 15}, got {len(df)}")
        return None

    d = df.copy()

    # Base features (scale-invariant)
    d["close_pct"]    = d["Close"].pct_change() * 100
    d["high_low_pct"] = (d["High"] - d["Low"]) / d["Close"].shift(1) * 100
    d["vol_ratio"]    = d["Volume"] / d["Volume"].rolling(20).mean().replace(0, 1)

    tr = pd.concat([
        (d["High"] - d["Low"]),
        (d["High"] - d["Close"].shift(1)).abs(),
        (d["Low"]  - d["Close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    d["atr_pct"] = tr.rolling(14).mean() / d["Close"] * 100

    delta = d["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean().replace(0, 1e-9)
    d["rsi"] = 100 - (100 / (1 + gain / loss))
    d["adx_proxy"] = tr.rolling(14).mean() / d["Close"] * 1000

    d["hour"]    = d.index.hour
    d["minute"]  = d.index.minute
    d["weekday"] = d.index.dayofweek

    # Lag features
    for lag in range(1, LAG_BARS + 1):
        d[f"cp_lag{lag}"]  = d["close_pct"].shift(lag)
        d[f"vr_lag{lag}"]  = d["vol_ratio"].shift(lag)
        d[f"rsi_lag{lag}"] = d["rsi"].shift(lag)
        d[f"hl_lag{lag}"]  = d["high_low_pct"].shift(lag)

    d.dropna(inplace=True)
    if d.empty:
        return None

    # Use the last row (current bar)
    row = d.iloc[[-1]][_feature_cols]
    return row


# ── Public API ────────────────────────────────────────────────────────────────
def compute_dl_rating(df: pd.DataFrame) -> dict:
    """
    Runs live ML inference on the last N 15m candles.
    Returns a rating dict compatible with intraday_scheduler and backtest_intraday.

    Entry is only STRONG_BUY/SELL when ALL THREE signals agree:
      direction is UP/DOWN  AND  vol surge likely  AND  vega expansion likely.
    This prevents entering when the option premium is likely to decay.
    """
    neutral = {"score": 0.0, "rating": "NEUTRAL", "direction": "NONE", "breakdown": {}}

    if _dir_model is None:
        log.error("❌ ML models not loaded. Run: python -m algo_trading.ml_trainer")
        return neutral

    try:
        row = _build_live_features(df)
        if row is None:
            return neutral

        # Predict class labels
        dir_pred  = int(_dir_model.predict(row)[0])     # -1, 0, or +1
        vol_pred  = int(_vol_model.predict(row)[0])     # 0 or 1
        vega_pred = int(_vega_model.predict(row)[0])    # 0 or 1

        # Predict probabilities for richer breakdown
        dir_proba  = _dir_model.predict_proba(row)[0]   # [P(down), P(neutral), P(up)]
        vol_proba  = _vol_model.predict_proba(row)[0]   # [P(no), P(yes)]
        vega_proba = _vega_model.predict_proba(row)[0]  # [P(no), P(yes)]

        # Map direction probabilities
        dir_classes = list(_dir_model.classes_)
        p_up    = dir_proba[dir_classes.index(1)]  if  1 in dir_classes else 0.0
        p_down  = dir_proba[dir_classes.index(-1)] if -1 in dir_classes else 0.0
        p_vol   = vol_proba[-1]
        p_vega  = vega_proba[-1]

        # Rating logic: need STRONG direction + volume surge + vega expansion
        # to justify buying an option (theta decay kills weak signals intraday)
        rating    = "NEUTRAL"
        direction = "NONE"
        score     = 0.0

        if dir_pred == 1 and vol_pred == 1 and vega_pred == 1:
            rating    = "STRONG_BUY"
            direction = "CALL"
            score     = p_up * 100

        elif dir_pred == -1 and vol_pred == 1 and vega_pred == 1:
            rating    = "STRONG_SELL"
            direction = "PUT"
            score     = p_down * -100

        elif dir_pred == 1 and (vol_pred == 1 or vega_pred == 1):
            rating    = "BUY"
            direction = "CALL"
            score     = p_up * 60

        elif dir_pred == -1 and (vol_pred == 1 or vega_pred == 1):
            rating    = "SELL"
            direction = "PUT"
            score     = p_down * -60

        return {
            "score":     round(score, 2),
            "rating":    rating,
            "direction": direction,
            "breakdown": {
                "ml_dir_pred":    dir_pred,
                "ml_vol_pred":    vol_pred,
                "ml_vega_pred":   vega_pred,
                "p_up":           round(p_up,   3),
                "p_down":         round(p_down,  3),
                "p_vol_surge":    round(p_vol,   3),
                "p_vega_expand":  round(p_vega,  3),
            },
        }

    except Exception as e:
        log.error(f"ML inference error: {e}")
        return neutral
