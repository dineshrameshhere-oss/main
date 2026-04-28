import logging
from datetime import datetime
import sys

# Custom colored formatter for Termux
class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[94m',       # Blue
        'INFO': '\033[92m',        # Green
        'WARNING': '\033[93m',     # Yellow
        'ERROR': '\033[91m',       # Red
        'CRITICAL': '\033[1;91m',  # Bold Red
        'RESET': '\033[0m'         # Reset
    }

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        time_str = datetime.now().strftime("%H:%M:%S")
        # Example format: [09:30] 📈 Message
        msg = f"{color}[{time_str}] {record.msg}{self.COLORS['RESET']}"
        record.msg = msg
        return super().format(record)

def setup_logger():
    logger = logging.getLogger("AlgoBot")
    logger.setLevel(logging.DEBUG)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    
    # Formatter (no standard logging level prefixes, keeping it clean as per plan)
    formatter = ColoredFormatter('%(message)s')
    ch.setFormatter(formatter)
    
    logger.addHandler(ch)
    return logger

log = setup_logger()
