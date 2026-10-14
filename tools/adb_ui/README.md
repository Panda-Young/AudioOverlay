# adb_ui / autopilot —— adb UI 自动化助手

用于 AudioOverlay 项目在 Android 真机/开发板（canoe / Redmi）上的自动化操作：
截图、文本定位、颜色/形状识别、自动点击与滑动。Windows 安全方式截图（不损坏 PNG）。

## 依赖

- Python 3.12（`C:\Users\16411\AppData\Local\Programs\Python\Python312\python.exe`）
- pillow / numpy / opencv-python（均已安装）
- adb 在 PATH 中

## 常用命令

```powershell
$py = "C:\Users\16411\AppData\Local\Programs\Python\Python312\python.exe"
$S = "192.168.0.112:46625"     # canoe

# 截图保存到电脑
& $py autopilot.py $S shot -o screen.png

# UI dump 并列出所有可见文本 + 坐标（用于文本定位）
& $py autopilot.py $S dump --list

# 按文本定位（输出中心坐标），--tap 直接点击
& $py autopilot.py $S find-text "Start Audio" --tap
& $py autopilot.py $S find-text "下一步" --tap

# 按颜色定位（#RRGGBB 或 R,G,B），--tap 点击
& $py autopilot.py $S find-color "#FFFFFF" --tap --tolerance 40

# 按颜色掩码找形状（矩形/圆形按钮），--tap 点击
& $py autopilot.py $S find-shape rect "#00FF00" --tap

# 直接点击 / 滑动
& $py autopilot.py $S tap 720 1600
& $py autopilot.py $S swipe 1150 2300 250 2300 300

# 启动应用（按包名）
& $py -c "import autopilot; autopilot.start_app('$S','com.android.chrome')"
```

## 库方式调用

```python
import autopilot
xy = autopilot.find_text("192.168.0.112:46625", "Start Audio")   # (cx,cy,label,bounds)
autopilot.tap("192.168.0.112:46625", *xy[:2])
c, area, img = autopilot.find_color("192.168.0.112:46625", "#FFFFFF")
```

## 识别说明

- **文本**：优先用 `uiautomator dump`（Android 原生精确文本 + 坐标），比 OCR 准且快；
  只对无 UI 语义的像素内容才需要图片 OCR（本工具未内置 OCR，可用 `dump --list` 代替）。
- **颜色**：OpenCV 颜色掩码 + 最大连通域质心，可调 `--tolerance`（默认 40）。
- **形状**：基于颜色掩码轮廓，`approxPolyDP` 判定矩形(4 顶点)/圆(≥8 顶点)。

## 坐标基准

坐标为设备物理像素（canoe 为 1440x3200），`input tap/swipe` 直接使用。
