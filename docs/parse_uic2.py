import re
s = open(r'C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\uic2.xml', encoding='utf-8').read()
for m in re.findall(r'<node[^>]*/?>', s):
    t = re.search(r'text="([^"]*)"', m)
    b = re.search(r'bounds="([^"]*)"', m)
    c = re.search(r'clickable="([^"]*)"', m)
    chk = re.search(r'checkable="([^"]*)"', m)
    chkd = re.search(r'checked="([^"]*)"', m)
    txt = (t.group(1) if t else '').strip()
    if txt or (c and c.group(1) == 'true') or (chk and chk.group(1) == 'true'):
        print(repr(txt), '|', (b.group(1) if b else ''), '|click=', (c.group(1) if c else ''), '|checkable=', (chk.group(1) if chk else ''), '|checked=', (chkd.group(1) if chkd else ''))
