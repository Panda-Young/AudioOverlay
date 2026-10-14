#!/usr/bin/env python3
"""Drive the MediaProjection consent flow over adb so capture can be started
without touching the screen.

Usage:
    python tools/grant_capture.py --serial 816d9a5 --app VLC

Steps automated:
    app Start Capture button
      -> "share a single app" radio
      -> Next
      -> app selector: tap the entry named --app
      -> final "Start now" confirmation (if present)

Why this exists: MediaProjection single-app consent cannot be granted by a
broadcast; P2 requires repeating the same flow on two devices many times.
The picker grid is dumped with `uiautomator dump` and matched by visible text,
which works on both canoe (Android 16) and MIUI.

Note from earlier attempts: tapping the large preview card at the top of the
selector does nothing — you must tap the icon/label entry in the list itself.
"""

import argparse
import re
import subprocess
import sys
import time


def adb(serial, *args, timeout=30):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout or ""


def nodes(serial):
    """Dump the UI hierarchy and return [(text, bounds, class), ...]."""
    adb(serial, "shell", "uiautomator", "dump", "/sdcard/_gc.xml")
    xml = adb(serial, "shell", "cat", "/sdcard/_gc.xml")
    out = []
    for m in re.finditer(r"<node\b[^>]*>", xml):
        n = m.group(0)

        def attr(k):
            mm = re.search(k + r'="([^"]*)"', n)
            return mm.group(1) if mm else ""

        t, b = attr("text"), attr("bounds")
        if t.strip():
            out.append((t.strip(), b, attr("class")))
    return out


def center(bounds):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def tap(serial, pt):
    adb(serial, "shell", "input", "tap", str(pt[0]), str(pt[1]))


def find(nodes_list, *needles):
    for t, b, c in nodes_list:
        for n in needles:
            if n in t:
                return t, b, c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--app", default="VLC", help="visible name in the app selector")
    ap.add_argument("--no-start", action="store_true", help="only run the consent flow")
    args = ap.parse_args()

    s = args.serial

    # 0. Bring the app to the foreground.
    adb(s, "shell", "am", "start", "-n", "com.example.audiooverlay/.MainActivity")
    time.sleep(2)

    # 1. Start Capture
    hit = find(nodes(s), "Start Capture")
    if not hit:
        print("[ERROR] 'Start Capture' button not found", file=sys.stderr)
        return 1
    tap(s, center(hit[1]))
    time.sleep(2)

    # 2. Choose "share a single app"
    for attempt in range(6):
        ns = nodes(s)
        hit = find(ns, "共享一个应用", "Share a single app")
        if hit:
            tap(s, center(hit[1]))
            time.sleep(1.5)
            # The row expands into a radio group on some builds; select explicitly.
            ns = nodes(s)
            hit2 = find(ns, "共享一个应用", "Share a single app")
            if hit2:
                tap(s, center(hit2[1]))
            break
        time.sleep(1)
    else:
        print("[ERROR] consent dialog not found", file=sys.stderr)
        return 1

    # 3. Next
    hit = find(nodes(s), "下一步", "Next")
    if not hit:
        print("[ERROR] 'Next' button not found", file=sys.stderr)
        return 1
    tap(s, center(hit[1]))
    time.sleep(3)

    # 4. Pick the target app in the selector
    for attempt in range(8):
        ns = nodes(s)
        if find(ns, "选择要分享的应用", "Choose an app"):
            hit = find(ns, args.app)
            if hit:
                tap(s, center(hit[1]))
                break
        time.sleep(1)
    else:
        print(f"[ERROR] app '{args.app}' not found in selector", file=sys.stderr)
        return 1
    time.sleep(3)

    # 5. Final confirmation (some builds skip straight to the target app)
    ns = nodes(s)
    hit = find(ns, "立即开始", "Start now", "立即分享")
    if hit:
        tap(s, center(hit[1]))
        time.sleep(3)

    # 6. Sanity check: capture should now be running
    out = adb(s, "shell", "dumpsys", "activity", "services", "com.example.audiooverlay")
    running = "AudioCaptureService" in out
    print(f"[OK] consent flow done; AudioCaptureService present={running}")
    return 0 if running else 2


if __name__ == "__main__":
    sys.exit(main())
