#!/usr/bin/env python3
"""Parse dumpsys media.audio_flinger output to extract session refs and signal power."""
import sys
import re

def read_file(path):
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:2] == b'\xff\xfe':
        return raw.decode('utf-16-le')
    elif raw[:3] == b'\xef\xbb\xbf':
        return raw.decode('utf-8-sig')
    else:
        return raw.decode('utf-8', errors='replace')

def extract_global_sessions(text):
    """Extract Global session refs table."""
    idx = text.find('Global session refs')
    if idx < 0:
        return []
    # Find the table after the header
    section = text[idx:idx+2000]
    lines = section.split('\n')
    sessions = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_table:
                break
            continue
        # Table header: session cnt pid uid name
        if 'session' in stripped.lower() and 'cnt' in stripped.lower() and 'pid' in stripped.lower():
            in_table = True
            continue
        if in_table:
            parts = stripped.split()
            if len(parts) >= 4 and parts[0].isdigit():
                sessions.append({
                    'session': int(parts[0]),
                    'cnt': parts[1] if len(parts) > 1 else '',
                    'pid': parts[2] if len(parts) > 2 else '',
                    'uid': parts[3] if len(parts) > 3 else '',
                    'name': ' '.join(parts[4:]) if len(parts) > 4 else ''
                })
            elif not stripped[0].isdigit():
                break
    return sessions

def extract_signal_power(text):
    """Extract latest Signal power history entries from each AudioOut thread."""
    results = []
    # Find all AudioOut_ thread sections
    thread_pattern = re.compile(r'(AudioOut_\w+)[^\n]*\n(.*?)(?=\n *AudioOut_|\n *AudioIn_|\n *Mixer|\Z)', re.DOTALL)
    for match in thread_pattern.finditer(text):
        thread_name = match.group(1)
        section = match.group(2)
        power_idx = section.find('Signal power history')
        if power_idx >= 0:
            # Get lines after the header
            power_section = section[power_idx:power_idx+500]
            power_lines = power_section.split('\n')
            # Find the latest (last non-empty numeric line)
            latest = None
            for pl in power_lines[1:]:
                pl_stripped = pl.strip()
                if pl_stripped and re.match(r'^[\d\s\.\-]+$', pl_stripped):
                    latest = pl_stripped
                elif pl_stripped and not re.match(r'^[\d\s\.\-]+$', pl_stripped):
                    if latest:
                        break
            if latest:
                results.append({'thread': thread_name, 'latest_power': latest})
    return results

def main():
    if len(sys.argv) < 2:
        print("Usage: parse_dumpsys.py <file> [--uid <uid>]")
        sys.exit(1)
    
    path = sys.argv[1]
    target_uid = None
    if '--uid' in sys.argv:
        target_uid = sys.argv[sys.argv.index('--uid') + 1]
    
    text = read_file(path)
    
    print("=== Global Session Refs ===")
    sessions = extract_global_sessions(text)
    for s in sessions:
        marker = " <-- TARGET" if target_uid and s['uid'] == target_uid else ""
        print(f"  session={s['session']:>5}  cnt={s['cnt']:>3}  pid={s['pid']:>6}  uid={s['uid']:>6}  name={s['name']}{marker}")
    
    print("\n=== Signal Power History (latest) ===")
    powers = extract_signal_power(text)
    for p in powers:
        print(f"  {p['thread']}: {p['latest_power']}")
    
    # Also find all power history lines for detailed view
    print("\n=== All Signal Power History sections ===")
    for m in re.finditer(r'(AudioOut_\w+).*?Signal power history.*?\n(.*?)(?=\n *\n|\Z)', text, re.DOTALL):
        thread = m.group(1)
        history = m.group(2).strip()
        lines = [l.strip() for l in history.split('\n') if l.strip() and re.match(r'^[\d\s\.\-]+$', l.strip())]
        if lines:
            print(f"  {thread}:")
            for l in lines[-3:]:
                print(f"    {l}")

if __name__ == '__main__':
    main()
