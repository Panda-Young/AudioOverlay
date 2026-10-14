#!/usr/bin/env python3
"""
session_remount.py — Session 动态重挂自动化工具

轮询 `adb shell dumpsys media.audio_flinger` → 解析 Global session refs 表 →
按 uid 过滤目标 App → 发现新 session 自动发 MUTE_SESSION 广播 → 日志输出。

解决问题：抖音等 App 切视频/重播时 session ID 递增（如 81→89），
旧 DynamicsProcessing 静音效果成 Orphan，需自动检测新 session 并重挂静音。

用法:
    python session_remount.py --serial 816d9a5 --uid 10205 --interval 3
    python session_remount.py --serial 192.168.0.109:37807 --uid 10205 --action com.example.audiooverlay.MUTE_SESSION
    python session_remount.py --offline docs\canoe_mute_baseline.txt --uid 10205  # 离线解析验证

参数:
    --serial    设备序列号 (adb -s <serial>)
    --uid       目标 App 的 uid (如抖音 10205, Chrome 10118)
    --interval  轮询间隔秒数 (默认 3)
    --action    MUTE_SESSION 广播 action (默认 com.example.audiooverlay.MUTE_SESSION)
    --offline   离线模式：解析指定 dumpsys txt 文件，不发广播，用于验证解析逻辑
    --once      只轮询一次，用于测试
"""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime


def read_file(path):
    """读取文件，自动处理 UTF-16 (PowerShell 重定向默认编码)。"""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le")
    elif raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8", errors="replace")


def run_adb(serial, cmd):
    """执行 adb 命令并返回 stdout。"""
    full_cmd = ["adb", "-s", serial] + cmd
    result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
    return result.stdout


def parse_global_sessions(dumpsys_text):
    """
    解析 Global session refs 表，返回 session 列表。
    表格式:
      Global session refs:
        session  cnt     pid    uid  name
             49    1   16368  10205  com.ss.android.ugc.aweme
             89    1   16368  10205  com.ss.android.ugc.aweme
    """
    sessions = []
    idx = dumpsys_text.find("Global session refs")
    if idx < 0:
        return sessions

    section = dumpsys_text[idx:idx + 2000]
    lines = section.split("\n")
    in_table = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_table:
                break
            continue
        # 检测表头
        if "session" in stripped.lower() and "cnt" in stripped.lower() and "pid" in stripped.lower():
            in_table = True
            continue
        if in_table:
            parts = stripped.split()
            if len(parts) >= 4 and parts[0].isdigit():
                sessions.append({
                    "session": int(parts[0]),
                    "cnt": parts[1],
                    "pid": parts[2],
                    "uid": parts[3],
                    "name": " ".join(parts[4:]) if len(parts) > 4 else "",
                })
            elif not stripped[0].isdigit():
                break
    return sessions


def filter_by_uid(sessions, target_uid):
    """按 uid 过滤 session。"""
    target_str = str(target_uid)
    return [s for s in sessions if s["uid"] == target_str]


def send_mute_broadcast(serial, action, sid, mute=True):
    """发送 MUTE_SESSION 广播。"""
    cmd = [
        "shell", "am", "broadcast",
        "-a", action,
        "--ei", "sid", str(sid),
        "--ez", "mute", "true" if mute else "false",
    ]
    output = run_adb(serial, cmd)
    return output


def log(msg):
    """带时间戳的日志输出。"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def offline_mode(filepath, target_uid):
    """离线解析模式：解析 dumpsys txt 文件，不发广播。"""
    log(f"离线模式: 解析 {filepath}")
    text = read_file(filepath)
    all_sessions = parse_global_sessions(text)
    log(f"Global session refs 共 {len(all_sessions)} 个 session")

    for s in all_sessions:
        marker = " <-- TARGET" if s["uid"] == str(target_uid) else ""
        log(f"  session={s['session']:>5}  uid={s['uid']:>6}  pid={s['pid']:>6}  name={s['name']}{marker}")

    target_sessions = filter_by_uid(all_sessions, target_uid)
    log(f"目标 uid={target_uid} 的 session: {[s['session'] for s in target_sessions]}")
    return target_sessions


def poll_mode(serial, target_uid, interval, action, once=False):
    """轮询模式：持续监控 session 变化，自动重挂静音。"""
    log(f"轮询模式: serial={serial}, uid={target_uid}, interval={interval}s")
    log(f"广播 action: {action}")
    log("按 Ctrl+C 停止")

    muted_sessions = set()  # 已静音的 session ID 集合

    try:
        while True:
            # 抓 dumpsys
            dumpsys_text = run_adb(serial, ["shell", "dumpsys", "media.audio_flinger"])
            all_sessions = parse_global_sessions(dumpsys_text)
            target_sessions = filter_by_uid(all_sessions, target_uid)

            current_ids = {s["session"] for s in target_sessions}
            new_ids = current_ids - muted_sessions
            gone_ids = muted_sessions - current_ids

            if gone_ids:
                log(f"检测到 session 消失 (Orphan): {sorted(gone_ids)}")
                muted_sessions -= gone_ids

            if new_ids:
                log(f"检测到新 session: {sorted(new_ids)}")
                for sid in sorted(new_ids):
                    log(f"  发送静音: sid={sid}")
                    output = send_mute_broadcast(serial, action, sid, mute=True)
                    if "result=0" in output:
                        log(f"  ✓ sid={sid} 静音已挂载")
                        muted_sessions.add(sid)
                    else:
                        log(f"  ✗ sid={sid} 静音失败: {output.strip()}")
            else:
                log(f"当前 session: {sorted(current_ids)} (已静音 {len(muted_sessions)} 个)")

            if once:
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        log("\n收到中断信号，停止轮询")
        log(f"共静音过 {len(muted_sessions)} 个 session: {sorted(muted_sessions)}")


def main():
    parser = argparse.ArgumentParser(
        description="Session 动态重挂自动化：轮询 dumpsys，检测新 session 自动发 MUTE_SESSION 广播"
    )
    parser.add_argument("--serial", help="设备序列号 (adb -s <serial>)")
    parser.add_argument("--uid", type=int, required=True, help="目标 App 的 uid (如抖音 10205)")
    parser.add_argument("--interval", type=int, default=3, help="轮询间隔秒数 (默认 3)")
    parser.add_argument("--action", default="com.example.audiooverlay.MUTE_SESSION",
                        help="MUTE_SESSION 广播 action")
    parser.add_argument("--offline", help="离线模式：解析指定 dumpsys txt 文件")
    parser.add_argument("--once", action="store_true", help="只轮询一次")

    args = parser.parse_args()

    if args.offline:
        offline_mode(args.offline, args.uid)
    elif args.serial:
        poll_mode(args.serial, args.uid, args.interval, args.action, args.once)
    else:
        parser.error("轮询模式需要 --serial，或使用 --offline 离线模式")


if __name__ == "__main__":
    main()
