import os
import sys
from dotenv import load_dotenv

from .logger import log
from .scheduler import start_scheduler
from .trade_executor import get_balance


def print_banner(live_mode: bool = False, is_intraday: bool = False):
    load_dotenv()   # ensure env is loaded before reading balance
    balance  = get_balance(live=live_mode)
    mode_str = '🔴 LIVE TRADING' if live_mode else '🟢 PAPER TRADING'
    bot_type = 'DEEP LEARNING INTRADAY' if is_intraday else 'OPTIONS SCALPING'
    
    print('\033[96m' + '='*52)
    print(f'  🚀 NIFTY 50 {bot_type} BOT')
    print('='*52 + '\033[0m')
    print(f'  Mode    : {mode_str}')
    print(f'  Balance : ₹{balance:.2f}')
    print(f'  AI      : Google Gemini 2.0-Flash')
    print('='*52)


def main():
    load_dotenv()

    if '--startScalp' in sys.argv:
        print_banner(live_mode=False, is_intraday=False)
        log.info('⚡ --startScalp: Paper mode auto-starting...')
        start_scheduler(live_mode=False)
        return
        
    if '--startIntraday' in sys.argv:
        from algo_trading.intraday_scheduler import start_intraday_scheduler
        print_banner(live_mode=False, is_intraday=True)
        log.info('🧠 --startIntraday: DL Intraday Paper mode auto-starting...')
        start_intraday_scheduler(live_mode=False)
        return

    if '--live' in sys.argv:
        is_intraday = '--intraday' in sys.argv
        print_banner(live_mode=True, is_intraday=is_intraday)
        confirm = input('⚠️  TYPE "YES-LIVE" TO CONFIRM REAL MONEY TRADING: ').strip()
        if confirm != 'YES-LIVE':
            print('Aborted. Start again without --live for paper mode.')
            return
        log.warning('🔴 LIVE TRADING MODE ENGAGED.')
        if is_intraday:
            from algo_trading.intraday_scheduler import start_intraday_scheduler
            start_intraday_scheduler(live_mode=True)
        else:
            start_scheduler(live_mode=True)
        return

    # Interactive mode
    print_banner(live_mode=False)
    ans = input('▶️  Start LIVE trading? (type YES to confirm, anything else = paper): ').strip()
    live_mode = (ans == 'YES')
    
    bot_ans = input('▶️  Start DL Intraday bot instead of Scalper? (type YES to confirm): ').strip()
    is_intraday = (bot_ans == 'YES')
    
    if live_mode:
        log.warning('🔴 LIVE TRADING MODE ENGAGED.')
    else:
        log.info('🟢 Paper trading mode.')
        
    if is_intraday:
        from algo_trading.intraday_scheduler import start_intraday_scheduler
        start_intraday_scheduler(live_mode)
    else:
        start_scheduler(live_mode)


if __name__ == '__main__':
    main()
