import pandas as pd
from algo_trading.config import EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD, VOLUME_MULT_SCALP
from algo_trading.market_data import _fetch_indstocks_chart
import numpy as np

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def run_backtest():
    print("Fetching up to 60 days of 5m Nifty data from INDMoney for backtesting...")
    df = _fetch_indstocks_chart(interval='5minute', days_back=60)
    
    if df is None or df.empty:
        print("Failed to fetch data from INDMoney. Please check if your INDSTOCKS_TOKEN in .env is valid and active.")
        return
        
    df = df.dropna()
    print(f"Loaded {len(df)} 5-minute bars.")
    
    # Calculate indicators
    df['EMA_F'] = df['Close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['EMA_S'] = df['Close'].ewm(span=EMA_SLOW, adjust=False).mean()
    df['RSI'] = calculate_rsi(df['Close'], RSI_PERIOD)
    
    df['TradeDate'] = df.index.date
    df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['TPV'] = df['TP'] * df['Volume']
    
    df['Cum_Vol'] = df.groupby('TradeDate')['Volume'].cumsum()
    df['Cum_TPV'] = df.groupby('TradeDate')['TPV'].cumsum()
    df['VWAP'] = df['Cum_TPV'] / df['Cum_Vol']
    
    df['Vol_Avg'] = df['Volume'].rolling(20).mean()
    
    # Simulate trades
    in_trade = False
    trade_dir = 0
    entry_price = 0
    prev_long_score = 0
    prev_short_score = 0
    
    # Capital simulation
    capital = 2000.0
    total_deposited = 2000.0
    
    trades = []
    daily_trades = 0
    current_date = None
    
    start_date = df.index[0].date()
    last_deposit_date = start_date
    
    # Dynamic targets for current trade
    current_sl_pts = 0
    current_tp_pts = 0
    current_premium_target = 0
    
    for i in range(200, len(df)):
        row = df.iloc[i]
        date = row.name.date()
        
        # Monthly deposit check (approx 30 days)
        if (date - last_deposit_date).days >= 30:
            capital += 2000.0
            total_deposited += 2000.0
            last_deposit_date = date
            print(f"[{date}] Deposited Rs 2000. Total Capital: Rs {capital:.2f}")
        
        if date != current_date:
            current_date = date
            daily_trades = 0
            # EOD exit check
            if in_trade:
                # EOD Exit
                exit_pnl_pts = (row['Close'] - entry_price) * trade_dir
                # Translate back to INR using the delta at entry
                # premium_change = exit_pnl_pts * delta
                # But to keep it simple, we just cap the loss at SL and win at TP, otherwise linear
                delta = min(0.8, max(0.2, current_premium_target / 300.0))
                exit_pnl_inr = exit_pnl_pts * delta * 25
                capital += exit_pnl_inr
                trades.append({
                    "date": date, "type": "LONG" if trade_dir == 1 else "SHORT",
                    "entry": entry_price, "exit": row['Close'],
                    "pnl_inr": exit_pnl_inr, "capital": capital, "premium": current_premium_target
                })
                in_trade = False
                
        # 1. Manage Open Trade
        if in_trade:
            exit_hit = False
            exit_price = 0
            exit_pts = 0
            
            if trade_dir == 1: # LONG
                if row['Low'] <= entry_price - current_sl_pts:
                    exit_hit = True; exit_price = entry_price - current_sl_pts; exit_pts = -current_sl_pts
                elif row['High'] >= entry_price + current_tp_pts:
                    exit_hit = True; exit_price = entry_price + current_tp_pts; exit_pts = current_tp_pts
            else: # SHORT
                if row['High'] >= entry_price + current_sl_pts:
                    exit_hit = True; exit_price = entry_price + current_sl_pts; exit_pts = -current_sl_pts
                elif row['Low'] <= entry_price - current_tp_pts:
                    exit_hit = True; exit_price = entry_price - current_tp_pts; exit_pts = current_tp_pts
                    
            if exit_hit:
                delta = min(0.8, max(0.2, current_premium_target / 300.0))
                exit_pnl_inr = exit_pts * delta * 25
                capital += exit_pnl_inr
                trades.append({
                    "date": date, "type": "LONG" if trade_dir == 1 else "SHORT",
                    "entry": entry_price, "exit": exit_price,
                    "pnl_inr": exit_pnl_inr, "capital": capital, "premium": current_premium_target
                })
                in_trade = False
                continue
                
            # EOD Force close at 15:15
            if in_trade and row.name.time().hour == 15 and row.name.time().minute >= 15:
                exit_pnl_pts = (row['Close'] - entry_price) * trade_dir
                delta = min(0.8, max(0.2, current_premium_target / 300.0))
                exit_pnl_inr = exit_pnl_pts * delta * 25
                capital += exit_pnl_inr
                trades.append({
                    "date": date, "type": "LONG" if trade_dir==1 else "SHORT",
                    "entry": entry_price, "exit": row['Close'],
                    "pnl_inr": exit_pnl_inr, "capital": capital, "premium": current_premium_target
                })
                in_trade = False
                continue
            
        # 2. Look for Entry
        if daily_trades >= 3: continue
        time_h = row.name.time().hour
        time_m = row.name.time().minute
        
        is_morning = (time_h == 9 and time_m >= 30) or (time_h == 10) or (time_h == 11 and time_m <= 30)
        is_afternoon = (time_h == 13) or (time_h == 14 and time_m <= 45)
        if not (is_morning or is_afternoon): continue
        
        # User Strategy: Always 1 lot (25 qty). Buy the best option budget allows.
        # Max budget = current capital. Target premium = budget / 25
        premium_target = capital / 25.0
        
        if premium_target < 20: 
            # If account is blown to < 500 Rs, can't realistically buy anything safe.
            continue
            
        long_score = 0
        short_score = 0
        
        ema_f = row['EMA_F']
        ema_s = row['EMA_S']
        rsi = row['RSI']
        vwap = row['VWAP']
        price = row['Close']
        vol = row['Volume']
        vol_avg = row['Vol_Avg']
        
        ema_200 = df['Close'].iloc[i-200:i].mean()
        
        if ema_f > ema_s: long_score += 1
        elif ema_f < ema_s: short_score += 1
            
        if price > vwap: long_score += 1
        elif price < vwap: short_score += 1
            
        if 55 < rsi < RSI_OVERBOUGHT: long_score += 1
        elif 45 > rsi > RSI_OVERSOLD: short_score += 1
            
        vol_confirm = vol > (vol_avg * VOLUME_MULT_SCALP)
        if vol_confirm:
            if long_score > 0: long_score += 0.5
            if short_score > 0: short_score += 0.5
            
        if price < ema_200: long_score = 0
        if price > ema_200: short_score = 0
            
        if long_score >= 2.0 and prev_long_score < 2.0:
            in_trade = True; trade_dir = 1
        elif short_score >= 2.0 and prev_short_score < 2.0:
            in_trade = True; trade_dir = -1
            
        if in_trade:
            entry_price = price
            daily_trades += 1
            current_premium_target = premium_target
            
            # Calculate dynamic SL/TP in Index Points based on Option Delta
            # Very rough delta approximation: OTM is low delta, ATM (~150 premium) is ~0.5 delta, ITM is high.
            est_delta = min(0.8, max(0.2, current_premium_target / 300.0))
            
            # SL is 10% of Premium, TP is 20% of Premium
            sl_prem = current_premium_target * 0.10
            tp_prem = current_premium_target * 0.20
            
            # Index points needed to move premium by SL/TP
            current_sl_pts = sl_prem / est_delta
            current_tp_pts = tp_prem / est_delta
            
        prev_long_score = long_score
        prev_short_score = short_score

    # Analysis
    if not trades:
        print("No trades executed.")
        return

    wins = [t for t in trades if t['pnl_inr'] > 0]
    losses = [t for t in trades if t['pnl_inr'] <= 0]
    
    total_inr = capital - total_deposited
    
    print("\n" + "="*50)
    print("📈 QUALITY-COMPOUNDING BACKTEST RESULTS (60 Days)")
    print("Strategy: 1 Fixed Lot, Upgrading Option Quality (Delta)")
    print("="*50)
    print(f"Total Trades:      {len(trades)}")
    print(f"Win Rate:          {len(wins)/len(trades)*100:.1f}%")
    print(f"Wins:              {len(wins)}")
    print(f"Losses:            {len(losses)}")
    print(f"Total Deposited:   ₹ {total_deposited:.2f}")
    print(f"Final Capital:     ₹ {capital:.2f}")
    print(f"Net PnL:           ₹ {total_inr:.2f}")
    print(f"Return on Invest:  {(total_inr/total_deposited)*100:.1f}%")
    print("="*50)

if __name__ == "__main__":
    run_backtest()
