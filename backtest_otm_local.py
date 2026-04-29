"""
Standalone OTM Backtest — Tests new high-profit strategy
Doesn't require INDMoney API token or market_data module
Uses synthetic data to validate the OTM strategy improvements
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys

# Config constants (updated for OTM strategy)
EMA_FAST = 3
EMA_SLOW = 8
RSI_PERIOD = 14
DEFAULT_SL_PCT = 0.05  # Tight 5%
DEFAULT_TP_PCT = 0.25  # Aggressive 25%
RATING_STRONG_BUY = 0.55  # Stricter entry
RATING_STRONG_SELL = -0.55

def generate_synthetic_data(days=60):
    """Generate realistic 5m NIFTY data."""
    dates = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    
    price = 24000
    current_date = datetime(2026, 2, 28)  # 60 days back
    
    for day in range(days):
        day_date = current_date + timedelta(days=day)
        if day_date.weekday() >= 5:  # skip weekends
            continue
        
        for bar in range(75):  # 75 bars per day
            hour = 9 + bar // 12
            minute = 15 + (bar % 12) * 5
            if minute >= 60:
                hour += 1
                minute -= 60
            if hour > 15 or (hour == 15 and minute > 30):
                break
            
            dt = day_date.replace(hour=hour, minute=minute)
            
            # Random momentum with bursts
            drift = np.random.normal(0, 0.01)
            burst = 0.03 if np.random.random() < 0.15 else 0
            move = drift + burst
            
            o = price
            c = price * (1 + move)
            h = max(o, c) * (1 + abs(np.random.normal(0, 0.008)))
            l = min(o, c) * (1 - abs(np.random.normal(0, 0.008)))
            v = int(np.random.normal(50000, 10000))
            
            dates.append(dt)
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            volumes.append(max(1000, v))
            price = c
    
    df = pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': closes, 'Volume': volumes
    }, index=pd.DatetimeIndex(dates, name='Date'))
    return df

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def run_otm_backtest(df, capital=5000):
    """Run backtest with new OTM strategy."""
    
    print(f"OTM Strategy Backtest | Capital: Rs {capital} | Data: {len(df)} candles")
    print("="*70)
    
    # Indicators
    df['RSI'] = calculate_rsi(df['Close'], RSI_PERIOD)
    df['EMA_F'] = df['Close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['EMA_S'] = df['Close'].ewm(span=EMA_SLOW, adjust=False).mean()
    
    trades = []
    balance = capital
    in_trade = False
    trade_data = {}
    entry_date = None
    entry_price = None
    premium = None
    qty = None
    peak_pnl = 0
    
    for i in range(50, len(df)):
        row = df.iloc[i]
        now = row.name
        
        # Skip non-market hours
        if now.hour < 9 or (now.hour == 15 and now.minute > 30) or now.hour > 15:
            continue
        
        # Manage open trade
        if in_trade:
            bar_hold_min = (now - entry_date).total_seconds() / 60
            
            # Nifty move from entry
            nifty_move = row['Close'] - entry_price
            
            # Option Greeks model (simplified)
            # OTM options have lower delta than ATM
            # delta_otm ≈ 0.35, gamma ≈ 0.002, theta ≈ -3.5 Rs/day linear
            delta = 0.35
            gamma = 0.002
            theta_per_min = -3.5 / (6.5 * 60)  # -3.5 Rs over 6.5 hours
            
            # Premium move calculation
            # premium_change = (nifty_move * delta) + (0.5 * nifty_move^2 * gamma) + (theta * minutes_held)
            gamma_gain = 0.5 * (nifty_move ** 2) * gamma
            theta_decay = theta_per_min * bar_hold_min
            premium_move = (nifty_move * delta) + gamma_gain + theta_decay
            
            current_premium = premium + premium_move
            pnl_pct = premium_move / premium if premium > 0 else 0
            
            if pnl_pct > peak_pnl:
                peak_pnl = pnl_pct
            
            # Exit conditions
            exit_signal = False
            exit_reason = ""
            exit_pnl = 0
            
            # TP: +25%
            if pnl_pct >= DEFAULT_TP_PCT:
                exit_signal = True
                exit_reason = "TP_HIT"
                exit_pnl = premium * DEFAULT_TP_PCT * qty
            
            # SL: -5%
            elif pnl_pct <= -DEFAULT_SL_PCT:
                exit_signal = True
                exit_reason = "SL_HIT"
                exit_pnl = -premium * DEFAULT_SL_PCT * qty
            
            # Time limit: 45 min (3 bars × 15min or 9 bars × 5min equivalent)
            elif bar_hold_min >= 45:
                exit_signal = True
                exit_reason = "TIME_EXIT"
                exit_pnl = premium_move * qty
            
            if exit_signal:
                balance += exit_pnl
                trades.append({
                    'entry': entry_price,
                    'exit_premium': current_premium,
                    'pnl': exit_pnl,
                    'pnl_pct': pnl_pct * 100,
                    'reason': exit_reason,
                    'hold_min': bar_hold_min
                })
                in_trade = False
                peak_pnl = 0
            
            continue
        
        # Look for entry
        rsi = row['RSI']
        ema_f = row['EMA_F']
        ema_s = row['EMA_S']
        close = row['Close']
        
        # Simple signal: momentum + trend
        rsi_signal = 1.0 if rsi > 70 else (-1.0 if rsi < 30 else 0)
        ema_signal = 1.0 if ema_f > ema_s else -1.0
        
        score = (rsi_signal + ema_signal) / 2
        
        # STRONG entry only
        signal = None
        if score >= RATING_STRONG_BUY and row['Volume'] > df['Volume'].rolling(20).mean().iloc[i] * 1.5:
            signal = "LONG"
        elif score <= RATING_STRONG_SELL and row['Volume'] > df['Volume'].rolling(20).mean().iloc[i] * 1.5:
            signal = "SHORT"
        
        if not signal:
            continue
        
        # OTM Premium estimation
        atm_premium = 100  # Simplified: assume ATM ≈ Rs 100 @ scaled price
        otm_premium = atm_premium * 0.15  # OTM is 15% of ATM (Rs 15)
        otm_premium_with_spread = otm_premium * 1.02  # 2% spread
        
        # Position sizing: use FULL account (aggressive)
        qty = max(1, int(balance / otm_premium_with_spread))
        required_capital = qty * otm_premium_with_spread
        
        if required_capital > balance:
            continue
        
        # Enter trade
        in_trade = True
        entry_date = now
        entry_price = close
        premium = otm_premium
        peak_pnl = 0
        
        print(f"[{now.strftime('%Y-%m-%d %H:%M')}] ENTRY {signal:5s} | Nifty {close:7.2f} | Premium Rs {premium:6.2f} | Qty {qty:3d} | Score {score:+.2f}")
    
    # Force close any remaining trade
    if in_trade:
        balance += premium * qty * -0.02  # Assume small loss
        trades.append({'entry': entry_price, 'exit_premium': premium * 0.98, 'pnl': premium * qty * -0.02, 'pnl_pct': -2, 'reason': 'EOD_CLOSE', 'hold_min': 30})
    
    # Results
    print("\n" + "="*70)
    print(f"RESULTS | Total Trades: {len(trades)}")
    if trades:
        wins = [t for t in trades if t['pnl'] > 0]
        print(f"Wins: {len(wins)} | Losses: {len(trades) - len(wins)}")
        print(f"Win Rate: {len(wins)/len(trades)*100:.1f}%")
        print(f"Avg Profit/Trade: Rs {sum(t['pnl'] for t in trades)/len(trades):.2f}")
        print(f"\nBalance: Rs {balance:.2f} (started with Rs {capital})")
        print(f"Total P&L: Rs {balance - capital:.2f}")
        print(f"Return: {(balance - capital) / capital * 100:+.1f}%")
        print("\nTop trades:")
        trades_sorted = sorted(trades, key=lambda x: x['pnl'], reverse=True)
        for t in trades_sorted[:5]:
            print(f"  {t['reason']:12s} | {t['pnl_pct']:+6.1f}% | Rs {t['pnl']:+8.2f} | {t['hold_min']:.0f} min")
    else:
        print("No trades executed!")

if __name__ == "__main__":
    df = generate_synthetic_data(days=60)
    print(f"Generated {len(df)} candles\n")
    run_otm_backtest(df, capital=5000)
