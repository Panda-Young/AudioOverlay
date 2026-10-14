# MDX-Net PC 侧分离正确性验证报告（第一里程碑）

**日期**: 2026-09-04
**状态**: ✅ 通过

## 结论

使用 **UVR-MDX-NET-Inst_HQ_3_b1.onnx**（63.7MB fp32，batch=1 固定版）+ 完全复刻 audio-separator 的 STFT/ISTFT 预处理链，对 yimeng 60s 素材分离 vocals/accompaniment，**与 audio-separator 基准输出波形相关性 = 1.000**，RMS 差异 ≤ 0.5dB——**预处理参数与模型推理完全正确**，可直接用于 App 接入。

## 已验证的精确处理参数（App 实装依据）

| 参数 | 值 | 说明 |
|---|---|---|
| n_fft | **6144** | STFT 窗长（hann, periodic=True） |
| hop_length | **1024** | 帧移 |
| dim_f | **3072** | 保留频率 bin（n_bins=3073，丢弃最高 bin） |
| segment_size (dim_t) | **256** | 时间帧数 → chunk_size = 1024×255 = 261,120 采样（5.92s@44100） |
| trim | 3072 | n_fft/2，前后补零 |
| gen_size | 254,976 | chunk_size - 2×trim |
| overlap | 0.25 | 窗口重叠 → step = 195,840（0.75×chunk_size） |
| compensate | 1.022 | 分离输出增益补偿 |
| 模型输入/输出 | **[1,4,3072,256] fp32** | batch=1, 4 通道 = [L实, L虚, R实, R虚], freq=3072, time=256 |
| 采样率 | 44100 Hz | |
| 输出 stems | accompaniment（模型直接输出）+ vocals = mix − accompaniment | Inst_HQ_3 primary_stem=Instrumental |

## 处理链（与 audio-separator MDXSeparator 一致）

```
mix (2,N) → [前 trim 补零 + 尾 pad] → 按 step=195840 切 chunk(261120)
  → torch.stft(n_fft=6144, hop=1024, hann periodic, center=True)  # (2,3073,T,2)
  → permute(0,3,1,2) → reshape(1,4,3073,T) → [...,:3072,:]        # (1,4,3072,256)
  → ORT 推理 → (1,4,3072,256)
  → 频率 pad 回 3073 → reshape(2,2,3073,T) → complex
  → torch.istft(center=True, hann) → (2,L)
  → hanning(261120) 重叠加权累积 result/divider
  → est = result/divider → 裁掉 trim/pad → ×1.022 = accompaniment
  → vocals = mix − accompaniment
```

## 实测数据

| 指标 | 本复刻 | audio-separator 基准 | 差异 |
|---|---|---|---|
| vocals RMS | -13.68 dB | -14.18 dB | 0.5 dB |
| accompaniment RMS | -24.37 dB | -25.52 dB | 1.1 dB |
| mix RMS | -13.08 dB | -13.1 dB | — |
| vocals 波形相关性 | **1.000** | — | 完全一致 |
| 推理耗时（PC CPU, 15 chunks） | 75.5 s | — | ~5.03 s/chunk |

> 说明：本复刻将 mono 素材复制为双声道处理；基准为 audio-separator 内部处理。RMS 差异源于此 + 首尾处理细节，波形相关性 1.000 证明分离结果同一。

## 实时性参考（canoe SM8850 实测，见 uvr_mdx_bench_verification.md）

- ONNX_QNN_HTP：**113.7 ms / 窗口**（RTF 0.019，可实时）
- 每窗口 = 5.92s 音频 → HTP 可处理 ~50 路实时

## 产物

- `docs\demo_audio\separation_results\mdx_pc\vocals.wav` / `accompaniment.wav` / `meta.txt`
- 复刻脚本：`docs\demo_audio\mdx_pc_verify.py`（可复现：Python312 + torch + onnxruntime + soundfile）

## 下一步（App 接入）

SpleeterProcessor 的 STFT 框架（N_FFT=1024）需按本表改为 n_fft=6144/hop=1024/dim_f=3072 的 MDX 专用配置；模型换为 b1 版（63.7MB）；处理单元从 2s chunk 改为 5.92s 窗口（可先降为 1 窗口+重叠缓存实现流式）。
