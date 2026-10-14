import re, sys

def read_file(path):
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:2] == b'\xff\xfe':
        return raw.decode('utf-16-le')
    return raw.decode('utf-8', errors='replace')

path = sys.argv[1]
text = read_file(path)
lines = text.split('\n')

# Global session refs - find uid 10205 (Douyin)
print('=== Global session refs (uid=10205 Douyin) ===')
idx = text.find('Global session refs')
if idx >= 0:
    section = text[idx:idx+600]
    for l in section.split('\n'):
        if '10205' in l or 'session' in l.lower():
            print(l.rstrip())

# Find active AudioOut threads and latest power
print()
print('=== Active threads & latest power ===')
for i, l in enumerate(lines):
    if 'Output thread' in l:
        standby = lines[i+2].strip() if i+2 < len(lines) else ''
        if 'no' in standby.lower():
            thread_name = l.strip().split(',')[1].strip() if ',' in l else l.strip()
            print(f'ACTIVE: {thread_name}')
            for j in range(i, min(i+80, len(lines))):
                if 'Signal power history (resolution: 1000' in lines[j]:
                    for k in range(j+1, min(j+8, len(lines))):
                        pl = lines[k].strip()
                        if not pl:
                            continue
                        if 'Signal power' in pl:
                            break
                        if not pl[0].isdigit() and '09-' not in pl and '06-' not in pl and '08-' not in pl:
                            break
                        print(f'  {pl}')
                    break
