import pandas as pd
import yfinance as yf
import requests
import os
from .config import INDSTOCKS_BASE, NIFTY_SCRIP_CODE
from .logger import log

def get_auth_headers():
    token = os.getenv("INDSTOCKS_TOKEN", "")
    return {"Authorization": f"Bearer {token}"}

def fetch_historical_ohlcv(timeframes=['1mo', '1wk', '1h']):
    """
    Fetches historical OHLCV data using yfinance as fallback 
    (since IndStocks historical data often requires specific subscription).
    Symbol: ^NSEI (Nifty 50)
    """
    data = {}
    ticker = yf.Ticker("^NSEI")
    
    try:
        if '1mo' in timeframes:
            # Last 6 months
            df_mo = ticker.history(period="6mo", interval="1mo")
            data['1mo'] = df_mo
            
        if '1wk' in timeframes:
            # Last 12 weeks (~3 months)
            df_wk = ticker.history(period="3mo", interval="1wk")
            data['1wk'] = df_wk
            
        if '1h' in timeframes:
            # Last 5 days
            df_h = ticker.history(period="5d", interval="1h")
            data['1h'] = df_h
            
    except Exception as e:
        log.error(f"❌ Error fetching historical data: {e}")
        
    return data

def compress_ohlcv_to_string(df, timeframe, n_candles=5):
    """
    Compress DataFrame to string to save LLM tokens.
    Format: DATE|O|H|L|C|V|CHG%
    """
    if df is None or df.empty:
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

def fetch_intraday_data(interval='5m', period='1d'):
    """
    Fetches intraday data. For the bot, we mainly need 5m for SCALP.
    Using yfinance for reliable free data.
    """
    ticker = yf.Ticker("^NSEI")
    try:
        df = ticker.history(period=period, interval=interval)
        return df
    except Exception as e:
        log.error(f"❌ Error fetching intraday {interval} data: {e}")
        return pd.DataFrame()

def fetch_first_30min_candle():
    """
    Simulates fetching the first 30-min data of the day.
    """
    df = fetch_intraday_data(interval='30m', period='1d')
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
    Fetch Last Traded Price from IndStocks API.
    If fails, fallback to yfinance.
    """
    try:
        url = f"{INDSTOCKS_BASE}/market/quotes/ltp?scrip-codes={scrip_code}"
        res = requests.get(url, headers=get_auth_headers(), timeout=5)
        if res.status_code == 200:
            data = res.json()
            return float(data.get('data', {}).get(scrip_code, {}).get('ltp', 0))
    except Exception:
        pass
    
    # Fallback
    try:
        df = fetch_intraday_data(interval='1m', period='1d')
        if not df.empty:
            return float(df['Close'].iloc[-1])
    except:
        pass
        
    return 0.0
