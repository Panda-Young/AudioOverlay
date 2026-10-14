import subprocess, re, sys, time

SERIAL = '816d9a5'

def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout

def dump():
    sh(f'adb -s {SERIAL} shell uiautomator dump /sdcard/uiF.xml')
    return sh(f'adb -s {SERIAL} shell cat /sdcard/uiF.xml')

def nodes(xml):
    out = []
    for m in re.finditer(r'<node[^>]*?text="([^"]*)"[^>]*?clickable="(true|false)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        t, cl, x1, y1, x2, y2 = m.groups()
        out.append((t, cl == 'true', (int(x1)+int(x2))//2, (int(y1)+int(y2))//2))
    return out

def tap_text(target, tries=4, wait=2):
    for i in range(tries):
        xml = dump()
        ns = nodes(xml)
        hit = (next((n for n in ns if n[0] == target and n[1]), None)
               or next((n for n in ns if n[0] == target), None)
               or next((n for n in ns if target in n[0] and n[1]), None)
               or next((n for n in ns if target in n[0]), None))
        if hit:
            sh(f'adb -s {SERIAL} shell input tap {hit[2]} {hit[3]}')
            print(f'TAP[{target}] -> ({hit[2]},{hit[3]})')
            return True
        print(f'try{i+1}: [{target}] not found; visible={[n[0][:16] for n in ns if n[0]][:10]}')
        time.sleep(wait)
    return False

def broadcast(action, *extras):
    sh(f'adb -s {SERIAL} shell am broadcast -a {action} ' + ' '.join(extras))
    print(f'BCAST {action}')

if __name__ == '__main__':
    # 用法: python ui_flow.py <文本1> <文本2> ...
    # 广播由外层脚本发，这里只管 UI 点击
    for t in sys.argv[1:]:
        if not tap_text(t):
            print(f'FAIL: {t}')
        time.sleep(2)
