import subprocess, re, sys
path = sys.argv[1] if len(sys.argv) > 1 else '/sdcard/uiC.xml'
xml = subprocess.run(['adb','-s','816d9a5','shell','cat ' + path],capture_output=True,text=True).stdout
for m in re.finditer(r'<node[^>]*text="([^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
    t, x1, y1, x2, y2 = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
    print(f"'{t}' center=({(x1+x2)//2},{(y1+y2)//2})")
