"""
Quick Local Backtest — generates synthetic 5m Nifty data for testing new OTM strategy.
Doesn't depend on INDMoney API token.
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone, timedelta as td
import sys

IST = timezone(td(hours=5, minutes=30))

def generate_synthetic_nifty_5m(start_date, end_date, base_price=24000, volatility=0.015):
    """Generate realistic 5m NIFTY candles with momentum patterns."""
    dates = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    
    current_date = start_date
    price = base_price
    
    while current_date <= end_date:
        # Skip non-market hours
        if current_date.weekday() >= 5:  # weekends
            current_date += timedelta(days=1)
            continue
        
        # Trading hours: 9:15-15:30 IST (6h 15m = 75 5-min candles/day)
        hour, minute = 9, 15
        day_prices = []
        
        for _ in range(75):  # 75 candles × 5min = 375 min = 6h 15min
            if hour > 15 or (hour == 15 and minute > 30):
                hour, minute = 9, 15
                current_date += timedelta(days=1)
                if current_date.weekday() >= 5:
                    break
                continue
            
            bar_time = current_date.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=IST).replace(tzinfo=None)
            
            # Random walk with momentum bursts
            drift = np.random.normal(0, volatility)
            momentum_burst = 1.0 if np.random.random() < 0.15 else 0.0  # 15% chance of momentum
            move = drift + momentum_burst * np.random.normal(0, volatility * 3)
            
            open_price = price
            close_price = open_price * (1 + move)
            high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, volatility)))
            low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, volatility)))
            volume = int(np.random.normal(50000, 15000))
            
            dates.append(bar_time)
            opens.append(open_price)
            highs.append(high_price)
            lows.append(low_price)
            closes.append(close_price)
            volumes.append(max(1000, volume))
            
            price = close_price
            day_prices.append(price)
            minute += 5
            if minute >= 60:
                minute = 0
                hour += 1
        
        if current_date > end_date:
            break
    
    df = pd.DataFrame({
        'Date': dates,
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes
    })
    df.set_index('Date', inplace=True)
    return df

if __name__ == "__main__":
    print("Generating 60 days synthetic Nifty 5m data...")
    end_date = datetime(2026, 4, 28)
    start_date = end_date - timedelta(days=60)
    
    df = generate_synthetic_nifty_5m(start_date, end_date)
    print(f"Generated {len(df)} candles from {df.index[0]} to {df.index[-1]}")
    print(f"Price range: {df['Close'].min():.2f} - {df['Close'].max():.2f}")
    print("\nFirst 5 candles:")
    print(df.head())
    print("\nLast 5 candles:")
    print(df.tail())
