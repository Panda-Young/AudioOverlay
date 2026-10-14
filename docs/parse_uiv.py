import re
s = open(r'C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\uiv.xml', encoding='utf-8').read()
for m in re.findall(r'<node[^>]*/?>', s):
    cls = re.search(r'class="([^"]*)"', m)
    b = re.search(r'bounds="([^"]*)"', m)
    clk = re.search(r'clickable="([^"]*)"', m)
    if cls and ('VideoView' in cls.group(1) or 'video' in cls.group(1).lower() or (clk and clk.group(1) == 'true')):
        print((cls.group(1) if cls else ''), '|', (b.group(1) if b else ''), '|click=', (clk.group(1) if clk else ''))
