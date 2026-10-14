import re, sys

def read_file(path):
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:2] == b'\xff\xfe':
        return raw.decode('utf-16-le')
    return raw.decode('utf-8', errors='replace')

def get_latest_power(path):
    text = read_file(path)
    lines = text.split('\n')
    for i, l in enumerate(lines):
        if 'Output thread' in l:
            standby = lines[i+2].strip() if i+2 < len(lines) else ''
            if 'no' in standby.lower():
                for j in range(i, min(i+80, len(lines))):
                    if 'Signal power history (resolution: 1000' in lines[j]:
                        vals = []
                        for k in range(j+1, min(j+10, len(lines))):
                            pl = lines[k].strip()
                            if not pl:
                                continue
                            if 'Signal power' in pl:
                                break
                            if not pl[0].isdigit() and '09-' not in pl and '06-' not in pl:
                                break
                            nums = re.findall(r'-?\d+\.\d+', pl)
                            if nums:
                                vals.extend([float(x) for x in nums])
                        if vals:
                            return vals[-10:]
    return []

base = r'C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs'
files = [
    ('OFF_R1', base + r'\redmi_dsp_off_1.txt'),
    ('ON_R1',  base + r'\redmi_dsp_on_1.txt'),
    ('OFF_R2', base + r'\redmi_dsp_off_2.txt'),
    ('ON_R2',  base + r'\redmi_dsp_on_2.txt'),
]
for label, path in files:
    vals = get_latest_power(path)
    if vals:
        avg = sum(vals) / len(vals)
        mn = min(vals)
        mx = max(vals)
        print(f'{label}: avg={avg:.1f}dB min={mn:.1f} max={mx:.1f} n={len(vals)}')
        print(f'  latest5: {[round(v,1) for v in vals[-5:]]}')
    else:
        print(f'{label}: NO DATA')
