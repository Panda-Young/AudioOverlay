import autopilot, re
xml = autopilot.uidump('192.168.0.112:46625')
for m in re.findall(r'text="([^"]+)"', xml):
    if m.strip():
        print(m)
