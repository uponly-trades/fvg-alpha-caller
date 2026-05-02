import json, sys, re

data = json.load(sys.stdin)

EXCLUDE = {'XAU', 'XAG', 'PAXG', 'MSTR', 'BTCDOM'}

def is_valid(sym):
    base = sym[:-4]
    if base in EXCLUDE:
        return False
    if not re.match(r'^[A-Z0-9]{3,15}$', base):
        return False
    return True

valid = [d for d in data if d['symbol'].endswith('USDT') and is_valid(d['symbol'])]
sorted_by_vol = sorted(valid, key=lambda x: float(x['quoteVolume']), reverse=True)

for d in sorted_by_vol[:100]:
    print('    "%s",' % d['symbol'])
