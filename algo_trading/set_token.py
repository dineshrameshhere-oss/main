import sys
import os
from .logger import log

def update_indstocks_token(new_token: str):
    """
    Updates the INDSTOCKS_TOKEN in ALL .env files found in the project.
    """
    base_dir = os.path.join(os.path.dirname(__file__), '..')
    
    # List of possible .env locations
    env_paths = [
        os.path.join(base_dir, '.env'),
        os.path.join(base_dir, 'algo_trading', '.env')
    ]
    
    for env_path in env_paths:
        lines = []
        found = False
        
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
                
            with open(env_path, 'w') as f:
                for line in lines:
                    if line.startswith('INDSTOCKS_TOKEN='):
                        f.write(f'INDSTOCKS_TOKEN={new_token}\n')
                        found = True
                    else:
                        f.write(line)
                
                if not found:
                    if lines and not lines[-1].endswith('\n'):
                        f.write('\n')
                    f.write(f'INDSTOCKS_TOKEN={new_token}\n')
            
            log.info(f"✅ Updated: {os.path.abspath(env_path)}")
        else:
            log.debug(f"ℹ️ Skipping (not found): {env_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m algo_trading.set_token <YOUR_NEW_TOKEN>")
    else:
        update_indstocks_token(sys.argv[1])
