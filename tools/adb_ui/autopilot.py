# -*- coding: utf-8 -*-
"""
autopilot.py —— adb UI 自动化助手（截图 / 文本 / 颜色 / 形状识别 + 自动点击）

功能（CLI 子命令）：
  shot         adb 截图保存到电脑（Windows 安全方式，无损坏）
  dump         uiautomator dump 并解析为 (text, bounds) 列表
  find-text    在 UI dump 中按文本定位，返回中心坐标（可选 --tap 自动点击）
  find-color   在截图中按颜色定位，返回区域质心/最大连通块（可选 --tap）
  find-shape   在截图中按颜色掩码找矩形/圆，返回中心（可选 --tap）
  tap          直接点击 (x,y)
  swipe        滑动

用法示例：
  python autopilot.py shot 192.168.0.112:46625 -o screen.png
  python autopilot.py dump 192.168.0.112:46625 --list
  python autopilot.py find-text 192.168.0.112:46625 "Start Audio" --tap
  python autopilot.py find-color 192.168.0.112:46625 "#FFFFFF" --tap
  python autopilot.py find-shape 192.168.0.112:46625 rect "#00FF00" --tap

也可作为库使用：
  import autopilot
  xy = autopilot.find_text("192.168.0.112:46625", "Start Audio")
  autopilot.tap("192.168.0.112:46625", *xy)
"""
import argparse
import io
import re
import subprocess
import sys
import tempfile
import os

import numpy as np
import cv2
from PIL import Image

# ---------------- adb 基础 ----------------

def adb(serial, *args, timeout=60):
    """执行 adb 命令，返回 stdout 文本。"""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} 失败: {r.stderr.decode(errors='ignore')}")
    return r.stdout

def adb_bin(serial, *args, timeout=120):
    """执行 adb 命令，返回原始二进制。"""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} 失败: {r.stderr.decode(errors='ignore')}")
    return r.stdout

def devices():
    r = subprocess.run(["adb", "devices"], capture_output=True, timeout=30)
    out = r.stdout.decode(errors="ignore")
    res = []
    for line in out.splitlines():
        line = line.strip()
        if line and "\t" in line and "device" in line.split("\t")[1].split()[0] == "device":
            res.append(line.split("\t")[0])
    return res

# ---------------- 截图 ----------------

def screenshot(serial, out_path=None):
    """adb exec-out screencap -p 截图（Windows 下安全），返回 PIL Image。"""
    raw = adb_bin(serial, "exec-out", "screencap", "-p")
    img = Image.open(io.BytesIO(raw))
    if out_path:
        img.save(out_path)
        print(f"[shot] 已保存 {out_path}  ({img.width}x{img.height})")
    return img

# ---------------- UI dump / 文本识别 ----------------

def uidump(serial, local_path=None):
    """uiautomator dump 到设备，pull 到本地，返回 XML 文本。"""
    adb(serial, "shell", "uiautomator", "dump", "/sdcard/_autopilot_ui.xml")
    if local_path is None:
        fd, local_path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
    adb(serial, "pull", "/sdcard/_autopilot_ui.xml", local_path)
    with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def parse_bounds(bounds_str):
    """解析 '[x1,y1][x2,y2]' -> (cx, cy)。"""
    m = re.findall(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m[0])
    return ((x1 + x2) // 2, (y1 + y2) // 2)

def parse_ui_nodes(xml):
    """解析 UI dump XML，返回 [(text, cx, cy, bounds, clickable)]。"""
    nodes = []
    for m in re.finditer(
        r'<node[^>]*?text="([^"]*)"[^>]*?bounds="(\[[^"]*\])"[^>]*?(?:content-desc="([^"]*)")?[^>]*?/>',
        xml,
    ):
        text = m.group(1)
        bounds = m.group(2)
        content_desc = m.group(3) or ""
        label = text if text else content_desc
        if not label:
            continue
        center = parse_bounds(bounds)
        if center:
            nodes.append((label, center[0], center[1], bounds))
    return nodes

def find_text(serial, target, local_path=None, substring=False, case_sensitive=False):
    """在 UI dump 中按文本找元素，返回第一个匹配的中心坐标 (x,y)。"""
    xml = uidump(serial, local_path)
    nodes = parse_ui_nodes(xml)
    t = target if case_sensitive else target.lower()
    for label, cx, cy, bounds in nodes:
        cand = label if case_sensitive else label.lower()
        if (cand == t) or (substring and t in cand):
            return (cx, cy, label, bounds)
    return None

# ---------------- 颜色识别 ----------------

def parse_color(s):
    """解析颜色：#RRGGBB / #RGB / R,G,B / R G B -> (b,g,r) OpenCV 顺序。"""
    s = s.strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    else:
        parts = [int(x) for x in re.split(r"[,\s]+", s) if x != ""]
        if len(parts) == 3:
            r, g, b = parts
        else:
            raise ValueError(f"无法解析颜色: {s}")
    return (b, g, r)

def color_mask_cv(img, target_rgb, tolerance=40):
    """返回颜色掩码（numpy bool）。target_rgb=(r,g,b)。"""
    cv_img = np.array(img.convert("RGB"))[:, :, ::-1]  # RGB -> BGR
    bgr = (target_rgb[2], target_rgb[1], target_rgb[0])
    lower = np.array([max(0, c - tolerance) for c in bgr], dtype=np.uint8)
    upper = np.array([min(255, c + tolerance) for c in bgr], dtype=np.uint8)
    mask = cv2.inRange(cv_img, lower, upper)
    return mask, cv_img

def largest_blob_center(mask, min_area=50):
    """返回掩码中最大连通域的中心 (cx, cy) 和面积；无则 (None, 0)。"""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for c in contours:
        a = cv2.contourArea(c)
        if a > best_area:
            best_area = a
            best = c
    if best is None or best_area < min_area:
        return None, 0
    M = cv2.moments(best)
    if M["m00"] == 0:
        return None, 0
    cx = int(M["m10"] / M["m00"])
    cy = int(M["m01"] / M["m00"])
    return (cx, cy), best_area

def find_color(serial, color, tolerance=40, min_area=50, out_path=None):
    """在截图里找目标颜色区域，返回最大连通域中心 (x,y)。"""
    img = screenshot(serial, out_path)
    mask, _ = color_mask_cv(img, parse_color(color), tolerance)
    center, area = largest_blob_center(mask, min_area)
    return center, area, img

# ---------------- 形状识别 ----------------

def find_shape(serial, shape_type, color, tolerance=40, out_path=None):
    """基于颜色掩码找矩形或圆，返回中心坐标和面积。"""
    img = screenshot(serial, out_path)
    mask, _ = color_mask_cv(img, parse_color(color), tolerance)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for c in contours:
        a = cv2.contourArea(c)
        if a < 200:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        is_rect = shape_type == "rect" and len(approx) == 4
        is_circle = shape_type == "circle" and len(approx) >= 8
        if (is_rect or is_circle) and a > best_area:
            best_area = a
            best = c
    if best is None:
        return None, 0, img
    M = cv2.moments(best)
    cx = int(M["m10"] / M["m00"]) if M["m00"] else 0
    cy = int(M["m01"] / M["m00"]) if M["m00"] else 0
    return (cx, cy), best_area, img

# ---------------- 点击 / 滑动 ----------------

def tap(serial, x, y):
    adb(serial, "shell", "input", "tap", str(int(x)), str(int(y)))
    return (int(x), int(y))

def swipe(serial, x1, y1, x2, y2, ms=300):
    adb(serial, "shell", "input", "swipe", str(int(x1)), str(int(y1)),
        str(int(x2)), str(int(y2)), str(int(ms)))

def start_app(serial, package):
    """用 adb am start 启动应用（按包名启动主 Activity）。"""
    adb(serial, "shell", "monkey", "-p", package, "-c",
        "android.intent.category.LAUNCHER", "1")

def open_url(serial, url, browser_pkg=None):
    """用浏览器打开 URL。browser_pkg 可指定（默认系统浏览器）。"""
    if browser_pkg:
        adb(serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
            "-d", url, browser_pkg)
    else:
        adb(serial, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url)

# ---------------- CLI ----------------

def main():
    p = argparse.ArgumentParser(description="adb UI 自动化助手")
    p.add_argument("serial", help="adb 设备序列号，如 192.168.0.112:46625")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("shot", help="截图保存")
    sp.add_argument("-o", "--out", default="screen.png")
    sp.set_defaults(fn=lambda a: screenshot(a.serial, a.out))

    sp = sub.add_parser("dump", help="UI dump 并列出文本+坐标")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("-o", "--out")
    def _dump(a):
        xml = uidump(a.serial, a.out)
        nodes = parse_ui_nodes(xml)
        if a.list:
            for label, cx, cy, bounds in nodes:
                print(f"{cx:5d} {cy:5d}  {label}   {bounds}")
        print(f"[dump] 共 {len(nodes)} 个带文本节点")
    sp.set_defaults(fn=_dump)

    sp = sub.add_parser("find-text", help="按文本定位并可选点击")
    sp.add_argument("text")
    sp.add_argument("--tap", action="store_true", help="找到后自动点击")
    sp.add_argument("--substring", action="store_true")
    sp.add_argument("--case", action="store_true")
    sp.add_argument("-o", "--out")
    def _ft(a):
        r = find_text(a.serial, a.text, a.out, substring=a.substring,
                      case_sensitive=a.case)
        if r is None:
            print(f"[find-text] 未找到文本: {a.text}")
            sys.exit(1)
        cx, cy, label, bounds = r
        print(f"[find-text] '{label}' @ ({cx},{cy}) {bounds}")
        if a.tap:
            tap(a.serial, cx, cy)
            print(f"[tap] ({cx},{cy})")
    sp.set_defaults(fn=_ft)

    sp = sub.add_parser("find-color", help="按颜色定位并可选点击")
    sp.add_argument("color", help="#RRGGBB 或 R,G,B")
    sp.add_argument("--tap", action="store_true")
    sp.add_argument("--tolerance", type=int, default=40)
    sp.add_argument("--min-area", type=int, default=50)
    sp.add_argument("-o", "--out")
    def _fc(a):
        center, area, _ = find_color(a.serial, a.color, a.tolerance, a.min_area, a.out)
        if center is None:
            print(f"[find-color] 未找到颜色 {a.color}")
            sys.exit(1)
        print(f"[find-color] 颜色 {a.color} 最大连通域中心 @ ({center[0]},{center[1]}) 面积={area}")
        if a.tap:
            tap(a.serial, *center)
            print(f"[tap] ({center[0]},{center[1]})")
    sp.set_defaults(fn=_fc)

    sp = sub.add_parser("find-shape", help="按颜色掩码找形状（rect/circle）")
    sp.add_argument("shape", choices=["rect", "circle"])
    sp.add_argument("color", help="#RRGGBB 或 R,G,B")
    sp.add_argument("--tap", action="store_true")
    sp.add_argument("--tolerance", type=int, default=40)
    sp.add_argument("-o", "--out")
    def _fs(a):
        center, area, _ = find_shape(a.serial, a.shape, a.color, a.tolerance, a.out)
        if center is None:
            print(f"[find-shape] 未找到 {a.shape} 颜色 {a.color}")
            sys.exit(1)
        print(f"[find-shape] {a.shape} @ ({center[0]},{center[1]}) 面积={area}")
        if a.tap:
            tap(a.serial, *center)
            print(f"[tap] ({center[0]},{center[1]})")
    sp.set_defaults(fn=_fs)

    sp = sub.add_parser("tap", help="直接点击")
    sp.add_argument("x", type=int)
    sp.add_argument("y", type=int)
    sp.set_defaults(fn=lambda a: print(f"[tap] ({a.x},{a.y})") or tap(a.serial, a.x, a.y))

    sp = sub.add_parser("swipe", help="滑动")
    sp.add_argument("x1", type=int); sp.add_argument("y1", type=int)
    sp.add_argument("x2", type=int); sp.add_argument("y2", type=int)
    sp.add_argument("--ms", type=int, default=300)
    sp.set_defaults(fn=lambda a: swipe(a.serial, a.x1, a.y1, a.x2, a.y2, a.ms))

    args = p.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()
