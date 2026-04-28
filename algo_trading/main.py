import os
import sys
from dotenv import load_dotenv

from .logger import log
from .scheduler import start_scheduler
from .trade_executor import get_balance


def print_banner(live_mode: bool = False):
    load_dotenv()   # ensure env is loaded before reading balance
    balance  = get_balance(live=live_mode)
    mode_str = '🔴 LIVE TRADING' if live_mode else '🟢 PAPER TRADING'
    print('\033[96m' + '='*52)
    print('  🚀 NIFTY 50 OPTIONS SCALPING BOT')
    print('='*52 + '\033[0m')
    print(f'  Mode    : {mode_str}')
    print(f'  Balance : ₹{balance:.2f}')
    print(f'  AI      : Google Gemini 2.0-Flash')
    print('='*52)


def main():
    load_dotenv()

    if '--startScalp' in sys.argv:
        print_banner(live_mode=False)
        log.info('⚡ --startScalp: Paper mode auto-starting...')
        start_scheduler(live_mode=False)
        return

    if '--live' in sys.argv:
        print_banner(live_mode=True)
        confirm = input('⚠️  TYPE "YES-LIVE" TO CONFIRM REAL MONEY TRADING: ').strip()
        if confirm != 'YES-LIVE':
            print('Aborted. Start again without --live for paper mode.')
            return
        log.warning('🔴 LIVE TRADING MODE ENGAGED.')
        start_scheduler(live_mode=True)
        return

    # Interactive mode
    print_banner(live_mode=False)
    ans = input('▶️  Start LIVE trading? (type YES to confirm, anything else = paper): ').strip()
    live_mode = (ans == 'YES')
    if live_mode:
        log.warning('🔴 LIVE TRADING MODE ENGAGED.')
    else:
        log.info('🟢 Paper trading mode.')
    start_scheduler(live_mode)


if __name__ == '__main__':
    main()
