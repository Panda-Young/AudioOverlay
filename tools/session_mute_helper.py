#!/usr/bin/env python3
"""
Session Mute Helper — PC-side assistant for per-session output replacement.

Why this exists:
  Android does not allow ordinary apps to discover other apps' audio session IDs
  (AudioManager.activePlaybackConfigurations returns [] on Android 12+ without
  MEDIA_CONTENT_CONTROL; dumpsys media.audio_flinger requires DUMP permission).
  This helper runs on a PC with adb access, polls dumpsys, parses the target UID's
  session ID, and forwards it to the App via the MUTE_SESSION broadcast.

Usage:
  python tools/session_mute_helper.py [--serial 816d9a5] [--uid 10205] [--interval 5]

  --serial    adb device serial (auto-detected if only one device)
  --uid       target app UID to mute (default: 10205 = 抖音 on canoe)
  --interval  poll interval in seconds (default: 5)
  --once      run once and exit (don't loop)

The App must already be running with capture started. The helper sends:
  am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid <ID> --ez mute true
when the target session changes, and mute=false for the previous session.
"""

import argparse
import re
import subprocess
import sys
import time


def run_adb(serial, *args):
    """Run an adb command and return stdout."""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout


def detect_serial():
    """Auto-detect the only connected device serial."""
    out = run_adb(None, "devices")
    serials = []
    for line in out.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    if len(serials) == 1:
        return serials[0]
    if len(serials) == 0:
        print("[ERROR] No adb devices connected", file=sys.stderr)
        sys.exit(1)
    print(f"[ERROR] Multiple devices connected: {serials}. Use --serial.", file=sys.stderr)
    sys.exit(1)


def parse_sessions_from_dumpsys(output, target_uid):
    """
    Parse ALL active session IDs for the target UID from dumpsys media.audio_flinger.

    Table format:
      session  cnt     pid    uid  name
        1609    1    4446  10205  com.ss.android.ugc.aweme
        1737    1    4446  10205  com.ss.android.ugc.aweme

    Returns a list of session IDs (int) for the target UID (may be empty).
    TikTok may pre-create multiple AudioTrack sessions when swiping videos,
    so ALL of them must be muted to prevent double-audio.
    """
    sessions = []
    in_table = False
    for line in output.splitlines():
        stripped = line.strip()
        if "Global session refs" in stripped:
            in_table = True
            continue
        if in_table:
            if stripped.startswith("session") and "cnt" in stripped:
                continue
            if not stripped:
                break
            match = re.match(r"^\s*(\d+)\s+\d+\s+\d+\s+(\d+)\s+(\S+)", line)
            if match:
                session_id = int(match.group(1))
                uid = int(match.group(2))
                if uid == target_uid:
                    sessions.append(session_id)
    return sessions


def send_mute_broadcast(serial, session_id, mute):
    """Send MUTE_SESSION broadcast to the App."""
    mute_str = "true" if mute else "false"
    action = "com.example.audiooverlay.MUTE_SESSION"
    cmd = [
        "am", "broadcast",
        "-a", action,
        "--ei", "sid", str(session_id),
        "--ez", "mute", mute_str,
    ]
    result = run_adb(serial, "shell", *cmd)
    status = "MUTE" if mute else "UNMUTE"
    if "Broadcast completed" in result:
        print(f"[OK] {status} session {session_id}")
    else:
        print(f"[WARN] {status} session {session_id} — {result.strip()}")


def main():
    parser = argparse.ArgumentParser(description="Session Mute Helper")
    parser.add_argument("--serial", default=None, help="adb device serial")
    parser.add_argument("--uid", type=int, default=10205, help="target app UID (default: 10205)")
    parser.add_argument("--interval", type=int, default=2, help="poll interval seconds (default: 2)")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    args = parser.parse_args()

    serial = args.serial or detect_serial()
    target_uid = args.uid
    interval = args.interval

    print(f"Session Mute Helper started")
    print(f"  device: {serial}")
    print(f"  target UID: {target_uid}")
    print(f"  interval: {interval}s")
    print(f"  mode: {'once' if args.once else 'loop'}")
    print()

    muted_sessions = set()  # track ALL sessions we have muted (TikTok may have multiple)

    while True:
        try:
            output = run_adb(serial, "shell", "dumpsys", "media.audio_flinger")
            active_sessions = set(parse_sessions_from_dumpsys(output, target_uid))

            # New sessions appeared -> mute them
            new_sessions = active_sessions - muted_sessions
            for sid in sorted(new_sessions):
                print(f"[INFO] New session {sid} appeared, muting")
                send_mute_broadcast(serial, sid, True)
                muted_sessions.add(sid)

            # Old sessions disappeared -> unmute them (cleanup)
            gone_sessions = muted_sessions - active_sessions
            for sid in sorted(gone_sessions):
                print(f"[INFO] Session {sid} disappeared, unmuting")
                send_mute_broadcast(serial, sid, False)
                muted_sessions.discard(sid)

            if not new_sessions and not gone_sessions:
                if active_sessions:
                    print(f"[OK] {len(active_sessions)} session(s) active: {sorted(active_sessions)} (all muted)")
                else:
                    print(f"[INFO] No active session for uid={target_uid} (app may be paused)")

        except subprocess.TimeoutExpired:
            print("[WARN] adb command timed out, retrying...")
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")

        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
