import os
import sys
from dotenv import load_dotenv

from .logger import log
from .scheduler import start_scheduler
from .trade_executor import get_balance


def print_banner(live_mode: bool = False, is_intraday: bool = False, use_ai: bool = False):
    load_dotenv()   # ensure env is loaded before reading balance
    balance  = get_balance(live=live_mode)
    mode_str = '🔴 LIVE TRADING' if live_mode else '🟢 PAPER TRADING'
    bot_type = 'DEEP LEARNING INTRADAY' if is_intraday else 'OPTIONS SCALPING'
    ai_status = 'Enabled' if use_ai else 'Disabled'
    
    # ── Auspicious Start ──────────────────────────────────────────────────
    print('\n  ௳')
    print('  ஓம் நம சிவாய')
    
    print('\033[96m' + '='*52)
    print(f'  🚀 NIFTY 50 {bot_type} BOT')
    print('='*52 + '\033[0m')
    print(f'  Mode    : {mode_str}')
    print(f'  Balance : ₹{balance:.2f}')
    print(f'  AI      : {ai_status}')
    print('='*52)


def main():
    load_dotenv()

    # Utility: Update INDSTOCKS_TOKEN
    if '--setToken' in sys.argv:
        try:
            token_idx = sys.argv.index('--setToken') + 1
            if token_idx < len(sys.argv):
                new_token = sys.argv[token_idx]
                from .set_token import update_indstocks_token
                update_indstocks_token(new_token)
            else:
                print("Usage: python -m algo_trading.main --setToken <YOUR_NEW_TOKEN>")
        except Exception as e:
            print(f"Error updating token: {e}")
        return

    use_ai = '--ai' in sys.argv

    if '--startScalp' in sys.argv:
        print_banner(live_mode=False, is_intraday=False, use_ai=use_ai)
        log.info(f'⚡ --startScalp: Paper mode auto-starting... (AI: {use_ai})')
        start_scheduler(live_mode=False, use_ai=use_ai)
        return
        
    if '--startIntraday' in sys.argv:
        from algo_trading.intraday_scheduler import start_intraday_scheduler
        print_banner(live_mode=False, is_intraday=True, use_ai=use_ai)
        log.info(f'🧠 --startIntraday: DL Intraday Paper mode auto-starting... (AI: {use_ai})')
        start_intraday_scheduler(live_mode=False, use_ai=use_ai)
        return

    if '--live' in sys.argv:
        is_intraday = '--intraday' in sys.argv
        print_banner(live_mode=True, is_intraday=is_intraday, use_ai=use_ai)
        confirm = input('⚠️  TYPE "YES-LIVE" TO CONFIRM REAL MONEY TRADING: ').strip()
        if confirm != 'YES-LIVE':
            print('Aborted. Start again without --live for paper mode.')
            return
        log.warning(f'🔴 LIVE TRADING MODE ENGAGED. (AI: {use_ai})')
        if is_intraday:
            from algo_trading.intraday_scheduler import start_intraday_scheduler
            start_intraday_scheduler(live_mode=True, use_ai=use_ai)
        else:
            start_scheduler(live_mode=True, use_ai=use_ai)
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
