# -*- coding: utf-8 -*-
"""更新 AudioOverlay 主汇总报告 report.html：追加第 17 章最新进展 + 更新 nav/header"""
import io, sys

P = r'C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\report.html'
s = open(P, encoding='utf-8').read()
orig_len = len(s)

# ---------- 1. nav 更新：补充 16/17 条目 ----------
old_nav = '<a href="#references">15. 参考资料</a>\n    </nav>'
new_nav = ('<a href="#references">15. 参考资料</a>\n'
           '      <a href="#ch16">16. 开源调研与 BGM 算法四模式真机验证</a>\n'
           '      <a href="#ch17">17. 最新进展汇总（2026-09-04）</a>\n    </nav>')
assert old_nav in s, "nav anchor not found"
s = s.replace(old_nav, new_nav)

# ---------- 2. header meta 更新 ----------
s = s.replace('📅 报告日期：2026-09-02', '📅 报告日期：2026-09-04（含最新进展）')
s = s.replace('📱 测试设备：模拟器 + Redmi 真机', '📱 测试设备：模拟器 + Redmi + canoe(SM8850)')
s = s.replace('🔧 工程版本：AudioRouteLab v1.0', '🔧 工程版本：AudioRouteLab v1.0 + Spleeter/Demucs 神经分离')

# ---------- 3. 追加第 17 章 ----------
ch17 = r'''    <!-- 第17章：最新进展汇总（2026-09-04） -->
    <section id="ch17" style="margin-top:48px;">
      <h2 style="color:var(--accent);">17. 最新进展汇总（2026-09-04）</h2>

      <div class="verdict-box" style="background:var(--green-bg); border:1px solid var(--green); border-radius:8px; padding:16px 20px; margin-bottom:20px;">
        <strong>里程碑</strong>：① per-session 静音机制真机验证成功（解决"双声叠加"，无需 Root/DUMP）；② Demucs htdemucs 与 UVR-MDX 两个神经分离方案通过听感/量化验收；③ canoe(SM8850) 发现 adb root 可用 + MediaProjection 授权自动化打通。
      </div>

      <h3>17.1 BGM 抑制算法多维实测对比（yimeng 60s 素材，验收基准 original.wav RMS -13.08dB）</h3>
      <table class="data-table">
        <thead><tr><th>方案</th><th>架构</th><th>人声RMS</th><th>伴奏RMS</th><th>BGM抑制</th><th>RTF</th><th>结论</th></tr></thead>
        <tbody>
          <tr><td><strong>Demucs htdemucs</strong></td><td>U-Net transformer</td><td>-13.5 dB</td><td>-26.3 dB</td><td><strong>~-12.8 dB</strong></td><td>0.59</td><td>✅ 用户听感验收"效果非常好"</td></tr>
          <tr><td><strong>UVR-MDX-NET-Inst_HQ_3</strong></td><td>MDX-Net</td><td>-14.18 dB</td><td>-25.52 dB</td><td><strong>-12.44 dB</strong></td><td>1.18</td><td>✅ 与 Demucs 相当，人声保留好</td></tr>
          <tr><td><strong>UVR-BVE-4B (aggr=5)</strong></td><td>VR</td><td>-22.33 dB</td><td>-14.93 dB</td><td>-1.85 dB</td><td>1.48</td><td>❌ 人声/伴奏轨疑似互换，不合格</td></tr>
          <tr><td><strong>Spleeter 2-stems fp16</strong></td><td>U-Net 复数谱</td><td>-26.2 dB</td><td>-12.1 dB</td><td>≈0 dB</td><td>0.24</td><td>❌ 语音类内容人声遗漏严重</td></tr>
        </tbody>
      </table>
      <p><strong>算法排序</strong>：Demucs ≥ UVR-MDX-Net ≫ Spleeter / UVR-VR。对<em>歌曲类</em>内容（处处吻）Spleeter 正常；对<em>朗诵/对白类</em>（以梦为马、血色湘西）Demucs/MDX 是可靠选择。</p>

      <h3>17.2 canoe(SM8850) 真机专项验证</h3>
      <table class="data-table">
        <thead><tr><th>能力</th><th>状态</th><th>证据/说明</th></tr></thead>
        <tbody>
          <tr><td>MediaProjection 授权闭环</td><td style="color:var(--green);">✅</td><td>Start Capture→下一步→选应用，autopilot 工具全自动</td></tr>
          <tr><td><strong>per-session 静音</strong></td><td style="color:var(--green);">✅ 真机验证</td><td>DynamicsProcessing(-200dB) attach Chrome session 249，audio_flinger 显示 Registered=y Enabled=y；解除正常。是"双声叠加"的解药，无需 Root/DUMP</td></tr>
          <tr><td>AudioPlaybackCapture 捕获</td><td style="color:var(--red);">❌ canoe 系统级</td><td>remote_submix loopback 无数据，usage/uid-only 均试过，Chrome 普通 track 播放中仍 -96dB；vendor audio policy/HAL 层限制</td></tr>
          <tr><td>adb root</td><td style="color:var(--purple);">🔑 可用</td><td>AOSP userdebug 自带 su，uid=0(root)，可系统级只读诊断</td></tr>
          <tr><td>应用就位</td><td style="color:var(--green);">✅</td><td>AudioOverlay(uid 10203)/Chrome(10118)/抖音(10205)/Shizuku 全装</td></tr>
        </tbody>
      </table>
      <p><strong>双声叠加解决方案</strong>（免 Root）：AudioPlaybackCapture 捕获 → DSP（Spleeter/Demucs/MDX）→ AudioTrack 回放处理声 → <strong>DynamicsProcessing per-session 静音（-200dB）消除原始声</strong> → 用户只听到处理后声音。canoe 捕获虽不可用，但静音机制已在真机确凿验证；Redmi（标准 PlaybackCapture）可完成全链路闭环。</p>

      <h3>17.3 Spleeter ONNX 实装（sherpa-onnx 2-stems fp16）</h3>
      <ul>
        <li><strong>工程</strong>：AudioCaptureService 新增 Spleeter 模式(mode 4)，2 秒 chunk 缓冲 + 协程异步 + AudioTrack 回放；FFT.kt/STFT.kt/Resampler.kt 纯 Kotlin 实现；模型 33.6MB（vocals+accompaniment fp16）</li>
        <li><strong>性能</strong>：真机稳定运行，RTF 0.31-0.46；QNN_CPU 基准 2924ms（RTF 0.375，比 CPU 快 1.60x）</li>
        <li><strong>局限</strong>：模型输入 [2,num_splits,512,1024] 复数频谱，对语音/对白类内容人声遗漏（音乐类正常）</li>
      </ul>

      <h3>17.4 QNN HTP / NPU 加速预研（SM8850 实测数据）</h3>
      <table class="data-table">
        <thead><tr><th>后端</th><th>htdemucs avg</th><th>RTF</th><th>加速比</th><th>状态</th></tr></thead>
        <tbody>
          <tr><td>ONNX_CPU (4线程)</td><td>4675 ms</td><td>0.60</td><td>1.00x</td><td>可跑，内存 4793MB 不可部署</td></tr>
          <tr><td>ONNX_XNNPACK</td><td>7746 ms</td><td>0.99</td><td>0.60x</td><td>更慢</td></tr>
          <tr><td><strong>ONNX_QNN_CPU</strong></td><td><strong>2924 ms</strong></td><td><strong>0.375</strong></td><td><strong>1.60x</strong></td><td>✅ 唯一可用加速</td></tr>
          <tr><td>ONNX_QNN_GPU</td><td>—</td><td>—</td><td>—</td><td>❌ ConvTranspose NHWC bug</td></tr>
          <tr><td>ONNX_QNN_HTP</td><td>—</td><td>—</td><td>—</td><td>❌ worker exit 134（需 fp16 模型）</td></tr>
        </tbody>
      </table>
      <p><strong>加速路径</strong>：htdemucs fp16 ONNX 转换是 HTP 破解第一手段（Path B 脚本已写好，含 op_block_list + 后处理，待执行）；QNN EP 配置参考 unified_model_bench（backend_type=htp, htp_performance_mode=burst, enable_htp_fp16_precision=1）。</p>

      <h3>17.5 工具与工程资产</h3>
      <ul>
        <li><strong>adb UI 自动化</strong>：<code>tools/adb_ui/autopilot.py</code>（shot/dump/find-text/find-color/find-shape/tap，OpenCV 掩码+连通域，已实测定位按钮）</li>
        <li><strong>模型资产</strong>：htdemucs.onnx(fp32 301.8MB)、Spleeter 2-stems fp16(33.6MB)、UVR-MDX(63.7MB)+UVR-BVE-4B(213.3MB)</li>
        <li><strong>测试素材</strong>：《以梦为马不负韶华》13:00-14:00 截取 60s（44100Hz），已 push 真机</li>
        <li><strong>定时监控</strong>：UVR 实测进度 cron 每 15 分钟巡检</li>
      </ul>

      <h3>17.6 当前并行任务（2026-09-04）</h3>
      <table class="data-table">
        <thead><tr><th>任务</th><th>状态</th><th>目标</th></tr></thead>
        <tbody>
          <tr><td>canoe 专项（静音实测/回放/root诊断）</td><td style="color:var(--orange);">🔄 运行中</td><td>对抖音/Chrome per-session 静音实测、AudioTrack 回放、根因报告</td></tr>
          <tr><td>UVR 实测 + 对比报告</td><td style="color:var(--orange);">🔄 运行中</td><td>vr_a15 档位 + Demucs/MDX/VR/Spleeter 最终对比报告</td></tr>
          <tr><td>htdemucs fp16 ONNX 转换</td><td style="color:var(--orange);">🔄 脚本就绪</td><td>Path B（op_block_list），为 QNN HTP 加速铺路</td></tr>
        </tbody>
      </table>
      <p><strong>待办</strong>：连接 Redmi(6dd1f4eb) 完成"捕获→分离→回放→静音"全链路真机闭环；fp16 转换重试；QNN HTP 集成。</p>
    </section>

'''
footer_comment = '    <!-- Footer -->'
assert footer_comment in s, "footer anchor not found"
s = s.replace(footer_comment, ch17 + footer_comment)

open(P, 'w', encoding='utf-8').write(s)
print('OK, size %d -> %d, added %d bytes' % (orig_len, len(s), len(s)-orig_len))
