# -*- coding: utf-8 -*-
"""PC-side session muter: poll dumpsys media.audio_flinger, auto-mount -200dB mute
on the ACTIVE session of the target app uid. Replaces in-app polling until the
app-side fix is built.
Usage: python session_muter.py <device_serial> <target_uid> [interval_s]
"""
import subprocess, re, sys, time

DEV = sys.argv[1] if len(sys.argv) > 1 else '816d9a5'
UID = sys.argv[2] if len(sys.argv) > 2 else '10205'
IVL = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

def adb(*args):
    return subprocess.run(['adb', '-s', DEV] + list(args), capture_output=True, text=True).stdout

def active_sessions(uid):
    """Parse track lines: find uid==target with Active=yes, return set of sessions."""
    out = adb('shell', 'dumpsys media.audio_flinger')
    sids = set()
    # Track line format: "<id> <Active> <pid>/<uid> <session> ..."
    for line in out.splitlines():
        m = re.match(r'\s*\d+\s+(yes|no)\s+(\d+)/\s*(\d+)\s+(\d+)\s', line)
        if not m:
            continue
        active, pid, app_uid, sid = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if app_uid == int(uid) and active == 'yes' and sid > 0:
            sids.add(sid)
    return sids

mounted = set()
last = None
while True:
    try:
        cur = active_sessions(UID)
        # mount new
        for sid in cur - mounted:
            r = adb('shell', 'am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid %d --ez mute true' % sid)
            print('[%s] MOUNT mute on session %d -> %s' % (time.strftime('%H:%M:%S'), sid, 'OK' if 'result=0' in r else 'FAIL'), flush=True)
        # unmount stale
        for sid in mounted - cur:
            r = adb('shell', 'am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid %d --ez mute false' % sid)
            print('[%s] UNMOUNT session %d' % (time.strftime('%H:%M:%S'), sid), flush=True)
        mounted = cur
        if cur != last:
            print('[%s] active sessions for uid=%s: %s' % (time.strftime('%H:%M:%S'), UID, sorted(cur) or '(none)'), flush=True)
            last = cur
    except Exception as e:
        print('[ERR]', e, flush=True)
    time.sleep(IVL)
