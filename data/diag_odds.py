import json
import time

def test():
    from betway import run as run_betway
    from onexbet import run as run_onexbet
    from sportybet import run as run_sportybet
    
    print("Fetching 1xBet...")
    b1 = run_onexbet()
    for m in b1:
        if 'Aston Villa' in m['home_team'] or 'Nottingham' in m['home_team']:
            print(json.dumps(m, indent=2))
            
    print("Fetching Betway...")
    b2 = run_betway()
    for m in b2:
        if 'Freiburg' in m['home_team']:
            print(json.dumps(m, indent=2))
            
if __name__ == '__main__':
    test()
