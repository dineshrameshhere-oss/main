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
