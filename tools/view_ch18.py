path = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\report.html"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()
idx = content.find('id="ch18"')
next_idx = content.find("<section id=", idx + 10)
if next_idx < 0:
    next_idx = idx + 5000
section = content[idx:next_idx]
for i, line in enumerate(section.split("\n")):
    if line.strip():
        print(f"{i:3d}: {line.strip()[:160]}")
