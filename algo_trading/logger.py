import logging
import sys
import os
from datetime import datetime

LOG_DIR  = os.path.join(os.path.dirname(__file__), '..', 'logs')
LOG_FILE = os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m%d')}.log")

class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG':    '\033[94m',
        'INFO':     '\033[92m',
        'WARNING':  '\033[93m',
        'ERROR':    '\033[91m',
        'CRITICAL': '\033[1;91m',
        'RESET':    '\033[0m',
    }
    def format(self, record):
        color    = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        time_str = datetime.now().strftime('%H:%M:%S')
        record.msg = f"{color}[{time_str}] {record.msg}{self.COLORS['RESET']}"
        return super().format(record)

class PlainFormatter(logging.Formatter):
    def format(self, record):
        time_str = datetime.now().strftime('%H:%M:%S')
        record.msg = f"[{time_str}] [{record.levelname}] {record.msg}"
        return super().format(record)

def setup_logger():
    logger = logging.getLogger('AlgoBot')
    if logger.handlers:          # avoid duplicate handlers on reimport
        return logger
    logger.setLevel(logging.DEBUG)

    # ── Console (coloured) ──────────────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(ColoredFormatter('%(message)s'))
    logger.addHandler(ch)

    # ── File (plain text — survives Termux session restart) ─────────────
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(PlainFormatter('%(message)s'))
        logger.addHandler(fh)
    except Exception as e:
        logger.warning(f"Could not create log file: {e}")

    return logger

log = setup_logger()
