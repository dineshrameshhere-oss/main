"""
ML Trainer for Intraday Nifty Options Bot
==========================================
Fetches 2 years of 15m Nifty data via INDMoney API,
engineers lag features + technicals, trains 3 scikit-learn models:

  Model 1: direction_model.pkl  — RandomForestClassifier (UP / DOWN / NEUTRAL)
  Model 2: vol_model.pkl        — GradientBoostingClassifier (volume surge: YES/NO)
  Model 3: vega_model.pkl       — GradientBoostingClassifier (volatility expansion: YES/NO)

Design decisions to avoid overfitting / underfitting:
  - Walk-forward split (first 70% train, last 30% test) — no random shuffle on time-series
  - 10-bar lag window (2.5 hrs of 15m data) — sweet spot for intraday; 20+ bars overfit
  - All features are scale-invariant (% returns, ratios, oscillators 0-100)
  - max_depth=8, min_samples_leaf=20 — hard limits prevent tree memorization
  - class_weight='balanced' — prevents model biasing toward NEUTRAL (most common class)

Run:
    python -m algo_trading.ml_trainer
"""
import os
import sys
import time
import pickle
import pathlib
import requests
import numpy as np
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

# ── Scikit-learn ───────────────────────────────────────────────────────────────
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import classification_report, accuracy_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(_ENV)

from .config import INDSTOCKS_BASE, NIFTY_SCRIP_CODE
from .logger import log

# ── Config ─────────────────────────────────────────────────────────────────────
LAG_BARS      = 10        # look back 10 × 15min = 2.5 hours
TRAIN_RATIO   = 0.70      # 70% train, 30% test (walk-forward — no shuffle)
MODELS_DIR    = pathlib.Path(__file__).parent.parent / "models"

# Target thresholds (scale-invariant %)
DIR_THRESHOLD = 0.30      # ±0.30% Nifty move in next 4 bars → UP/DOWN
VOL_THRESHOLD = 1.40      # volume > 1.4× 20-bar MA in next 4 bars → surge
VEGA_THRESHOLD = 1.20     # ATR > 1.2× current ATR in next 4 bars → expansion


# ─────────────────────────────────────────────────────────────────────────────
#  DATA FETCHING — 2 years of 5m → resampled to 15m
# ─────────────────────────────────────────────────────────────────────────────
def fetch_training_data(years: float = 2.0) -> pd.DataFrame:
    """
    Fetches `years` worth of 5m Nifty candles and resamples to 15m.
    Returns OHLCV DataFrame indexed by IST datetime.
    """
    token   = os.getenv("INDSTOCKS_TOKEN", "")
    headers = {"Authorization": token}

    days    = int(years * 365)
    end_ms  = int(time.time() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)

    all_candles = []
    chunk_end   = end_ms
    chunk_days  = 7   # fetch 7-day windows

    print(f"\n📥 Fetching {years:.0f} year(s) of 5m Nifty data for ML training...")

    while chunk_end > start_ms:
        chunk_start = max(start_ms, chunk_end - (chunk_days * 24 * 60 * 60 * 1000))
        params = {
            "scrip-codes": NIFTY_SCRIP_CODE,
            "start_time":  chunk_start,
            "end_time":    chunk_end,
        }
        url = f"{INDSTOCKS_BASE}/market/historical/5minute"

        try:
            res = requests.get(url, headers=headers, params=params, timeout=8)
            if res.status_code == 200:
                data    = res.json()
                candles = data.get("data", {}).get(NIFTY_SCRIP_CODE, {}).get("candles", [])
                if not candles:
                    break
                all_candles.extend(candles)
                oldest_ts  = candles[0].get("ts", 0) * 1000
                d_s = datetime.fromtimestamp(chunk_start / 1000).strftime("%Y-%m-%d")
                d_e = datetime.fromtimestamp(chunk_end   / 1000).strftime("%Y-%m-%d")
                print(f"  Chunk {d_s} → {d_e}: {len(candles)} bars | total: {len(all_candles)}")
                chunk_end = oldest_ts - 1000
                time.sleep(0.3)
            else:
                print(f"  API {res.status_code} — stopping fetch.")
                break
        except Exception as e:
            print(f"  Fetch error: {e} — stopping.")
            break

    if not all_candles:
        raise RuntimeError("No data fetched. Check INDSTOCKS_TOKEN in .env")

    df = pd.DataFrame(all_candles)
    df.drop_duplicates(subset=["ts"], inplace=True)
    df.sort_values("ts", ascending=True, inplace=True)
    df.rename(columns={"ts":"Timestamp","o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"}, inplace=True)
    df["Date"] = pd.to_datetime(df["Timestamp"], unit="s") + pd.Timedelta(hours=5, minutes=30)
    df.set_index("Date", inplace=True)

    # Resample 5m → 15m
    df_15 = df.resample("15min").agg(
        {"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}
    ).dropna()

    # Drop non-market hours
    df_15 = df_15.between_time("09:15", "15:30")
    print(f"\n✅ {len(df_15)} 15m bars loaded ({len(df_15)//25:.0f} trading days approx)")
    return df_15


# ─────────────────────────────────────────────────────────────────────────────
#  FEATURE ENGINEERING
# ─────────────────────────────────────────────────────────────────────────────
def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Builds scale-invariant features. All are percentages, ratios, or
    bounded oscillators (0-100) so model works whether Nifty is 1107 or 24000.
    """
    d = df.copy()

    # ── Base features ─────────────────────────────────────────────────────────
    # % return bar-over-bar (scale-invariant)
    d["close_pct"]  = d["Close"].pct_change() * 100
    d["high_low_pct"] = (d["High"] - d["Low"]) / d["Close"].shift(1) * 100

    # Volume ratio vs 20-bar MA
    d["vol_ratio"] = d["Volume"] / d["Volume"].rolling(20).mean().replace(0, 1)

    # ATR as % of price (scale-invariant)
    tr = pd.concat([
        (d["High"] - d["Low"]),
        (d["High"] - d["Close"].shift(1)).abs(),
        (d["Low"]  - d["Close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    d["atr_pct"] = tr.rolling(14).mean() / d["Close"] * 100

    # RSI (14-bar)
    delta = d["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean().replace(0, 1e-9)
    d["rsi"] = 100 - (100 / (1 + gain / loss))

    # ADX proxy: DI spread magnitude
    d["adx_proxy"] = tr.rolling(14).mean() / d["Close"] * 1000

    # Time features (intraday patterns matter — lunch dip, morning surge)
    d["hour"]     = d.index.hour
    d["minute"]   = d.index.minute
    d["weekday"]  = d.index.dayofweek   # 0=Mon, 4=Fri

    # ── Lag features (last LAG_BARS bars) ────────────────────────────────────
    for lag in range(1, LAG_BARS + 1):
        d[f"cp_lag{lag}"]  = d["close_pct"].shift(lag)
        d[f"vr_lag{lag}"]  = d["vol_ratio"].shift(lag)
        d[f"rsi_lag{lag}"] = d["rsi"].shift(lag)
        d[f"hl_lag{lag}"]  = d["high_low_pct"].shift(lag)

    return d


# ─────────────────────────────────────────────────────────────────────────────
#  TARGET ENGINEERING
# ─────────────────────────────────────────────────────────────────────────────
def engineer_targets(df: pd.DataFrame) -> pd.DataFrame:
    """
    Labels each bar with what happened in the NEXT 4 bars (1 hour ahead).
    Uses future data — only valid for training, never for live inference.
    """
    d = df.copy()
    fwd = 4   # predict 4 bars (1 hour) ahead

    # Target 1: Direction — future Nifty % move
    future_ret = (d["Close"].shift(-fwd) - d["Close"]) / d["Close"] * 100
    d["target_direction"] = 0   # NEUTRAL
    d.loc[future_ret >  DIR_THRESHOLD, "target_direction"] = 1   # UP
    d.loc[future_ret < -DIR_THRESHOLD, "target_direction"] = -1  # DOWN

    # Target 2: Volume Surge — will vol spike in next 4 bars?
    max_vol_fwd = d["vol_ratio"].rolling(fwd).max().shift(-fwd)
    d["target_vol"] = (max_vol_fwd > VOL_THRESHOLD).astype(int)

    # Target 3: Volatility Expansion (Vega proxy) — will ATR expand?
    max_atr_fwd = d["atr_pct"].rolling(fwd).max().shift(-fwd)
    d["target_vega"] = (max_atr_fwd > d["atr_pct"] * VEGA_THRESHOLD).astype(int)

    return d


# ─────────────────────────────────────────────────────────────────────────────
#  TRAINING
# ─────────────────────────────────────────────────────────────────────────────
FEATURE_COLS = (
    ["close_pct", "high_low_pct", "vol_ratio", "atr_pct", "rsi", "adx_proxy",
     "hour", "minute", "weekday"]
    + [f"cp_lag{i}"  for i in range(1, LAG_BARS + 1)]
    + [f"vr_lag{i}"  for i in range(1, LAG_BARS + 1)]
    + [f"rsi_lag{i}" for i in range(1, LAG_BARS + 1)]
    + [f"hl_lag{i}"  for i in range(1, LAG_BARS + 1)]
)


def _walk_forward_split(df: pd.DataFrame):
    """Time-series split: first TRAIN_RATIO rows = train, rest = test. No shuffle."""
    n     = len(df)
    split = int(n * TRAIN_RATIO)
    return df.iloc[:split], df.iloc[split:]


def train_and_save():
    """Full training pipeline. Prints evaluation report and saves .pkl models."""

    # ── 1. Fetch & prepare data ───────────────────────────────────────────────
    raw = fetch_training_data(years=2.0)
    raw = engineer_features(raw)
    raw = engineer_targets(raw)
    raw.dropna(inplace=True)

    X = raw[FEATURE_COLS]
    y_dir  = raw["target_direction"]
    y_vol  = raw["target_vol"]
    y_vega = raw["target_vega"]

    print(f"\n📊 Dataset: {len(X)} samples | {len(FEATURE_COLS)} features")
    print(f"   Train split: {int(len(X) * TRAIN_RATIO)} | Test split: {len(X) - int(len(X) * TRAIN_RATIO)}")
    print(f"   Direction class balance: {y_dir.value_counts().to_dict()}")

    X_train, X_test = X.iloc[:int(len(X)*TRAIN_RATIO)], X.iloc[int(len(X)*TRAIN_RATIO):]

    # ── 2. Model definitions ──────────────────────────────────────────────────
    # RandomForest for direction: robust to noise, handles 3-class imbalance
    # max_depth=8, min_samples_leaf=20 prevent memorization of training bars
    dir_model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(
            n_estimators     = 150,
            max_depth        = 8,
            min_samples_leaf = 20,
            class_weight     = "balanced",
            random_state     = 42,
            n_jobs           = -1,
        ))
    ])

    # GradientBoosting for vol/vega: better at detecting threshold-based patterns
    # learning_rate=0.05, n_estimators=100 = conservative to avoid overfit on binary targets
    vol_model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(
            n_estimators  = 100,
            max_depth     = 4,
            learning_rate = 0.05,
            subsample     = 0.8,
            random_state  = 42,
        ))
    ])

    vega_model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(
            n_estimators  = 100,
            max_depth     = 4,
            learning_rate = 0.05,
            subsample     = 0.8,
            random_state  = 42,
        ))
    ])

    # ── 3. Train ──────────────────────────────────────────────────────────────
    y_train_dir  = y_dir.iloc[:int(len(X)*TRAIN_RATIO)]
    y_train_vol  = y_vol.iloc[:int(len(X)*TRAIN_RATIO)]
    y_train_vega = y_vega.iloc[:int(len(X)*TRAIN_RATIO)]

    y_test_dir   = y_dir.iloc[int(len(X)*TRAIN_RATIO):]
    y_test_vol   = y_vol.iloc[int(len(X)*TRAIN_RATIO):]
    y_test_vega  = y_vega.iloc[int(len(X)*TRAIN_RATIO):]

    print("\n🔧 Training Direction model (RandomForest)...")
    dir_model.fit(X_train, y_train_dir)

    print("🔧 Training Volume Surge model (GradientBoosting)...")
    vol_model.fit(X_train, y_train_vol)

    print("🔧 Training Volatility Expansion model (GradientBoosting)...")
    vega_model.fit(X_train, y_train_vega)

    # ── 4. Evaluate on unseen test data ───────────────────────────────────────
    print("\n" + "=" * 60)
    print("📈 EVALUATION ON TEST SET (last 30% of data — unseen)")
    print("=" * 60)

    pred_dir  = dir_model.predict(X_test)
    pred_vol  = vol_model.predict(X_test)
    pred_vega = vega_model.predict(X_test)

    print(f"\n[Direction Model] Accuracy: {accuracy_score(y_test_dir, pred_dir):.2%}")
    print(classification_report(y_test_dir, pred_dir,
                                target_names=["DOWN(-1)", "NEUTRAL(0)", "UP(+1)"],
                                zero_division=0))

    print(f"\n[Volume Surge Model] Accuracy: {accuracy_score(y_test_vol, pred_vol):.2%}")
    print(classification_report(y_test_vol, pred_vol,
                                target_names=["No Surge", "Vol Surge"],
                                zero_division=0))

    print(f"\n[Volatility Expansion Model] Accuracy: {accuracy_score(y_test_vega, pred_vega):.2%}")
    print(classification_report(y_test_vega, pred_vega,
                                target_names=["No Expansion", "Vega Expansion"],
                                zero_division=0))

    # ── 5. Save models ────────────────────────────────────────────────────────
    MODELS_DIR.mkdir(exist_ok=True)

    with open(MODELS_DIR / "direction_model.pkl", "wb") as f:
        pickle.dump(dir_model,  f)
    with open(MODELS_DIR / "vol_model.pkl", "wb") as f:
        pickle.dump(vol_model,  f)
    with open(MODELS_DIR / "vega_model.pkl", "wb") as f:
        pickle.dump(vega_model, f)

    # Also save feature column list (needed by dl_engine to build live inference row)
    with open(MODELS_DIR / "feature_cols.pkl", "wb") as f:
        pickle.dump(FEATURE_COLS, f)

    print(f"\n✅ Models saved to: {MODELS_DIR}/")
    print("   direction_model.pkl | vol_model.pkl | vega_model.pkl")
    print("\nNext step: Run backtest → python -m algo_trading.backtest_intraday 5000")


if __name__ == "__main__":
    train_and_save()
