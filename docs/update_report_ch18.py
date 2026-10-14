# -*- coding: utf-8 -*-
P=r'C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\report.html'
s=open(P,encoding='utf-8').read()

# nav 追加 ch18
old_nav='<a href="#ch17">17. 最新进展汇总（2026-09-04）</a>\n    </nav>'
new_nav='<a href="#ch17">17. 最新进展汇总（2026-09-04）</a>\n      <a href="#ch18">18. 完整待办任务清单</a>\n    </nav>'
assert old_nav in s, 'nav ch17 anchor not found'
s=s.replace(old_nav,new_nav)

ch18=r'''    <!-- 第18章：完整待办任务清单 -->
    <section id="ch18" style="margin-top:48px;">
      <h2 style="color:var(--accent);">18. 完整待办任务清单（2026-09-04）</h2>
      <p>详细版见 <code>docs/TODO.md</code>（含每条任务说明、优先级、状态）。以下为按阶段的总览。</p>
      <table class="data-table">
        <thead><tr><th>阶段/主题</th><th>待办要点</th><th>优先级</th></tr></thead>
        <tbody>
          <tr><td><strong>真机全链路闭环</strong></td><td>Redmi 接入后完成"捕获→分离→回放→静音"闭环；UsageStats 目标 App 检测；per-session 静音动态管理（session 变化自动重挂）；捕获自动触发</td><td>P0/P1</td></tr>
          <tr><td><strong>BGM 算法改进</strong></td><td>Demucs/MDX 接入 App(mode 5)；htdemucs fp16 转换（沙箱阻塞待重试）；QNN HTP 加速实测；4-stem 对白场景；VAD 辅助；频谱门限法；流式 chunk 优化；INT8 量化</td><td>P0/P1/P2</td></tr>
          <tr><td><strong>免 Root 输出替换</strong></td><td>效果冲突处理；缓冲避免 underrun；Shizuku API 集成；隐藏 API（IPlayer.setVolume/AudioFocus ducking）研究；蓝牙 SCO 虚拟路由研究</td><td>P1-P3</td></tr>
          <tr><td><strong>Overlay 悬浮工具箱</strong></td><td>SYSTEM_ALERT_WINDOW 悬浮窗；AccessibilityService 前台检测；视频 App 自动识别；BGM 滑杆/开关/预设 UI；横竖屏适配；Service 保活</td><td>P1/P2</td></tr>
          <tr><td><strong>Root + AudioEffect（长期）</strong></td><td>Root 设备只读探测(Task 11)；研究 JamesDSP/Viper AIDL；自定义 effect .so；Magisk 注册（需谨慎评估红线）；验证 DirectOutput/Offload/MMAP 覆盖</td><td>P2</td></tr>
          <tr><td><strong>测试覆盖</strong></td><td>B站/腾讯视频捕获测试；YouTube(需 GMS)；低端设备性能测试</td><td>P1/P2</td></tr>
          <tr><td><strong>性能优化</strong></td><td>模式2 FFT 优化(512/KissFFT/NEON)；混合模式级联；QNN HTP 全链路</td><td>P2</td></tr>
          <tr><td><strong>工具/工程</strong></td><td>autopilot.py 完善为成熟 skill；测试素材库扩充；听感评分机制；gopeed/代理利用</td><td>P2/P3</td></tr>
          <tr><td><strong>文档/汇报</strong></td><td>子代理成果汇总入主报告；fp16/QNN 实测数据补充</td><td>P1</td></tr>
        </tbody>
      </table>
      <h3>最高优先路径</h3>
      <ol>
        <li><strong>Redmi 全链路闭环</strong>（#4）——接入后完成用户可验收的完整演示</li>
        <li><strong>Demucs/MDX 接入 App</strong>（#8）——替换 Spleeter 作为语音/对白场景主引擎</li>
        <li><strong>fp16 转换 + QNN HTP</strong>（#9/#10）——神经分离 offload 到 NPU，释放 CPU</li>
      </ol>
      <p>统计：🔄 进行中 3 项、❌ 阻塞 1 项、⬜ 待办 30 项、⏸ 搁置 12 项。</p>
    </section>

'''
fc='    <!-- Footer -->'
assert fc in s,'footer anchor not found'
s=s.replace(fc,ch18+fc)
open(P,'w',encoding='utf-8').write(s)
print('OK report.html ->', len(s.encode('utf-8')), 'bytes')
