# Session 动态重挂自动化文档

**日期**: 2026-09-04  
**脚本**: `tools\session_remount.py`  
**问题**: 抖音等 App 切视频/重播时 audio session ID 递增（如 81→89），旧 DynamicsProcessing 静音效果成 Orphan，无法自动迁移到新 session  
**解决方案**: 轮询 dumpsys → 解析 Global session refs → 按 uid 过滤 → 发现新 session 自动发 MUTE_SESSION 广播

## 脚本功能

1. **轮询** `adb shell dumpsys media.audio_flinger`（可配置间隔）
2. **解析** Global session refs 表（表头: session/cnt/pid/uid/name）
3. **过滤** 按目标 uid 筛选 App 的 session
4. **检测** 新 session 出现（对比已静音集合）
5. **自动静音** 对新 session 发送 `MUTE_SESSION` 广播（--ei sid N --ez mute true）
6. **Orphan 检测** 检测已静音 session 消失，从集合移除
7. **日志输出** 带时间戳的操作日志

## 参数说明

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--serial` | 轮询模式必填 | - | 设备序列号 (adb -s <serial>) |
| `--uid` | ✅ | - | 目标 App 的 uid（如抖音 10205, Chrome 10118） |
| `--interval` | ❌ | 3 | 轮询间隔秒数 |
| `--action` | ❌ | com.example.audiooverlay.MUTE_SESSION | 静音广播 action |
| `--offline` | ❌ | - | 离线模式：解析指定 dumpsys txt 文件，不发广播 |
| `--once` | ❌ | false | 只轮询一次（用于测试） |

## 使用示例

### 1. 在线轮询模式（canoe 抖音自动静音）

```powershell
python tools\session_remount.py --serial 816d9a5 --uid 10205 --interval 3
```

输出示例:
```
[14:30:01] 轮询模式: serial=816d9a5, uid=10205, interval=3s
[14:30:01] 广播 action: com.example.audiooverlay.MUTE_SESSION
[14:30:01] 按 Ctrl+C 停止
[14:30:02] 检测到新 session: [49, 89]
[14:30:02]   发送静音: sid=49
[14:30:02]   ✓ sid=49 静音已挂载
[14:30:02]   发送静音: sid=89
[14:30:03]   ✓ sid=89 静音已挂载
[14:30:06] 当前 session: [49, 89] (已静音 2 个)
[14:30:09] 检测到 session 消失 (Orphan): [89]
[14:30:09] 检测到新 session: [97]
[14:30:09]   发送静音: sid=97
[14:30:10]   ✓ sid=97 静音已挂载
```

### 2. Redmi 抖音自动静音

```powershell
python tools\session_remount.py --serial 192.168.0.109:37807 --uid 10205 --interval 5
```

### 3. 离线解析验证（不连接设备）

```powershell
python tools\session_remount.py --offline docs\canoe_mute_baseline2.txt --uid 10205
```

输出:
```
[04:25:05] 离线模式: 解析 docs\canoe_mute_baseline2.txt
[04:25:05] Global session refs 共 7 个 session
[04:25:05]   session=   41  uid=  1000  pid=  2749  name=android.uid.system
[04:25:05]   session=   49  uid= 10205  pid= 16368  name=com.ss.android.ugc.aweme <-- TARGET
[04:25:05]   session=   73  uid= 10131  pid=  3441  name=android.uid.systemui
[04:25:05]   session=   89  uid= 10205  pid= 16368  name=com.ss.android.ugc.aweme <-- TARGET
[04:25:05]   session=   89  uid=  1041  pid=  1810  name=audioserver
[04:25:05]   session=   89  uid= 10203  pid= 25310  name=com.example.audiooverlay
[04:25:05]   session=   49  uid= 10203  pid= 25310  name=com.example.audiooverlay
[04:25:05] 目标 uid=10205 的 session: [49, 89]
```

### 4. 单次轮询测试

```powershell
python tools\session_remount.py --serial 816d9a5 --uid 10205 --once
```

## 关键 UID 参考

| App | 包名 | UID | 设备 |
|-----|------|-----|------|
| 抖音 | com.ss.android.ugc.aweme | 10205 | canoe / Redmi |
| Chrome | com.android.chrome | 10118 | canoe |
| Edge | com.microsoft.emmx | - | Redmi |
| AudioOverlay | com.example.audiooverlay | 10203 | canoe |
| system | android.uid.system | 1000 | - |
| systemui | android.uid.systemui | 10131 | - |
| audioserver | audioserver | 1041 | - |

## Session ID 递增规律

实测 canoe 抖音 session ID 变化:
- 初始: session 49, 81
- 切视频后: session 49, **89** (81→89, +8)
- 规律: 主 session (49) 保持不变，副 session 随视频切换递增

旧 session 的 DynamicsProcessing 效果不会自动迁移，成为 Orphan（效果仍占用资源但不作用于新 session）。session_remount.py 通过轮询检测新 session 并自动重挂静音。

## 实现细节

### Global session refs 表解析

表格式（Android 16 / SM8850）:
```
Global session refs:
  session  cnt     pid    uid  name
       49    1   16368  10205  com.ss.android.ugc.aweme
       89    1   16368  10205  com.ss.android.ugc.aweme
```

解析逻辑:
1. 定位 "Global session refs" 字符串
2. 跳过表头行（含 session/cnt/pid）
3. 逐行解析，以空格分割，取前 5 列
4. 首列为数字且 >=4 列才视为有效行

### 编码处理

PowerShell `>` 重定向默认使用 UTF-16 LE 编码。脚本自动检测 BOM:
- `\xff\xfe` → UTF-16 LE
- `\xef\xbb\xbf` → UTF-8 BOM
- 其他 → UTF-8 (errors='replace')

### 静音广播

```
adb -s <serial> shell am broadcast -a com.example.audiooverlay.MUTE_SESSION --ei sid <N> --ez mute true
```

通过 `result=0` 判断广播是否成功送达。

## 注意事项

1. **MuteControlService 必须运行**: 静音广播由 MuteControlService 接收处理，需确保该服务在运行
2. **轮询间隔**: 建议 3-5 秒，太频繁增加系统负载，太迟钝可能漏过 session 变化
3. **多 session**: 抖音等 App 可能有多个活跃 session，脚本会自动全部静音
4. **Orphan 清理**: 脚本检测到 session 消失时从已静音集合移除，但不会主动 release 旧效果（MuteControlService onDestroy 时统一 release）
5. **离线模式**: 仅用于验证解析逻辑，不会发送任何广播
6. **Ctrl+C 停止**: 轮询模式下按 Ctrl+C 优雅退出，输出统计信息

## 相关文件

- `tools\session_remount.py` — 主脚本
- `tools\parse_dumpsys.py` — dumpsys 解析工具（单文件）
- `tools\parse_one.py` — 单文件解析（含 uid 过滤）
- `tools\compare_power.py` — 双文件功率对比
- `tools\compare_all.py` — 多文件功率统计
- `docs\canoe_douyin_mute_verification.md` — canoe 抖音静音验证报告（含 session 递增现象）
