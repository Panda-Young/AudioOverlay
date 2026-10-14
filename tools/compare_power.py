import sys

def read_file(path):
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:2] == b'\xff\xfe':
        return raw.decode('utf-16-le')
    return raw.decode('utf-8', errors='replace')

def get_active_power(path):
    text = read_file(path)
    lines = text.split('\n')
    results = []
    for i, l in enumerate(lines):
        if 'Output thread' in l:
            standby = lines[i+2].strip() if i+2 < len(lines) else ''
            if 'no' in standby.lower():
                thread = l.strip()
                powers = []
                for j in range(i, min(i+80, len(lines))):
                    if 'Signal power history (resolution: 1000' in lines[j]:
                        for k in range(j+1, min(j+6, len(lines))):
                            pl = lines[k].strip()
                            if not pl:
                                continue
                            if 'Signal power' in pl:
                                break
                            if not pl[0].isdigit() and '09-' not in pl and '06-' not in pl and '08-' not in pl:
                                break
                            powers.append(pl)
                        break
                results.append((thread, powers))
    return results

for label, path in [('DSP_OFF', sys.argv[1]), ('DSP_ON', sys.argv[2])]:
    print(f'=== {label} ===')
    for thread, powers in get_active_power(path):
        print(f'  {thread}')
        for p in powers:
            print(f'    {p}')
    print()
