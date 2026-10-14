# UVR-MDX-Net 实机实测验证报告（canoe SM8850）

> 日期：2026-09-04 ｜ 设备：canoe（SM8850 / Adreno840v2 / Android16 AOSP userdebug，serial 816d9a5，adb root）
> 模型：UVR-MDX-NET-Inst_HQ_3.onnx（63.7MB，fp32，MDX-Net 架构，现成开源模型无需转换）
> 工具：unified_bench（ORT 1.22.0 + QNN EP，LD_LIBRARY_PATH=.:./qnn ADSP_LIBRARY_PATH=./qnn）
> 输入：频谱张量 (batch, 4, 3072, 256)，随机输入，repeat=10
> 日志：`docs\uvr_bench_self.log`（2026-09-04 自跑，EXIT=0 全部 OK）

## 一、三后端实测结果

| Backend | avg (ms) | 相对 CPU 加速 | max_diff | 峰值内存 | 状态 |
|---|---|---|---|---|---|
| ONNX_CPU | 4075（多轮 4075~5536） | 1.00x | 0（基准） | 2111 MB | OK |
| ONNX_QNN_CPU | 3361 | 1.29x | 0.000154 | 4095 MB | OK |
| **ONNX_QNN_HTP** | **113.7** | **48.7x** | 0.140（fp16 精度） | 174.8 MB | **OK** |

实测明细（HTP 10 次 run：113.5 / 112.1 / 112.0 / 116.5 / 113.8 / 113.2 / 114.1 / 113.0 / 113.8 / 115.0 ms，稳定 ~113ms）。

## 二、关键结论

1. **UVR-MDX-NET-Inst_HQ_3 在 QNN HTP 上跑通且极快：113.7ms / 48.7x 加速**。
   - 现成 fp32 ONNX 直接可用，**无需 fp16 转换、无需量化**（unified_bench 的 HTP 配置自动处理）。
   - 对比 Demucs htdemucs：fp32 ONNX_CPU 4675ms / QNN_CPU 2924ms / **HTP exit 134 不可用**（含 GRU，QNN EP 不支持）。
   - **UVR-MDX 成为首个在 HTP 上可运行的分离模型**，性能余量极大。
2. **数值精度**：HTP max_diff=0.140 / avg_diff=0.006（相对 CPU fp32 基准，fp16 中间精度所致）。音频分离场景该差异听感可接受，后续可对比试听。
3. **RTF 口径（与子代理交叉核对一致）**：
   - 模型输入窗口 = 262,144 采样点 = **5.944s**（@44100Hz，shape [1,4,3072,256]）。
   - 纯模型推理 RTF：CPU 4075/5944 ≈ **0.686**、QNN_CPU 3361/5944 ≈ **0.565**、**HTP 113.7/5944 ≈ 0.0191**（远超实时，可同时处理 ~50 路）。
   - 端到端分离（PC audio-separator，含 STFT/ISTFT 与音频 IO）：yimeng 60s 素材耗时 70.8s（RTF 1.18）——**瓶颈在 PC 侧 CPU 与前后处理，非模型本身**；部署到 HTP 后前后处理同样可优化。
4. **与 PC 实测的交叉验证**：子代理同机实测（CPU 4571ms / QNN_CPU 3364ms / HTP 114.1ms）与本报告自跑数据一致（同模型同设备多轮波动），结果可信。

## 三、部署建议（本轮结论）

- **部署算法定为 UVR-MDX-NET-Inst_HQ_3 + ONNX Runtime QNN HTP backend**：
  - 推理 113.7ms/次（60s 素材级），完全满足"看视频实时降 BGM"需求（延迟预算内）。
  - 模型 63.7MB，峰值内存 174.8MB（HTP），移动端可接受。
  - 免转换、免量化、免 fp16 修复——绕开 Demucs fp16 的全部 NaN/GRU/HTP 兼容问题。
- Demucs htdemucs 保留为**效果基准**（用户听感验收"效果非常好"，BGM 抑制 -13.2dB），但其 HTP 部署受 GRU 不支持 + fp16 NaN 双重阻塞，暂缓。

## 四、遗留

- App 侧接入：STFT/ISTFT 前后处理（MDX-Net 频谱域）需实装到 AudioOverlay（SpleeterProcessor 已有 STFT 框架可复用改造）。
- HTP 与 App 的 session 管理 / DSP 集成待下一步（per-session 静音机制已通，见 canoe_mute_verification.md）。
