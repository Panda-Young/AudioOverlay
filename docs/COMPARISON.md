# 音频分离算法最终对比报告（yimeng 素材）

> 验收基准: `docs/demo_audio/yimeng/original.wav`（60s, 44100Hz, mono, 朗诵类, RMS -13.08dB）
> 目标: BGM 抑制（人声保留、伴奏压低），yimeng 是第一验收标准
> 生成时间: 2026-09-04（含 canoe SM8850 实机三后端实测 + fp16 NaN 根因定论）

## 1. 总览对比表（算法效果，PC 实测）

| 方案 | 人声 RMS | 伴奏 RMS | BGM抑制 | RTF(PC) | 模型体积 | 判定 |
|---|---|---|---|---|---|---|
| **Demucs htdemucs** | -13.5 dB | -26.3 dB | **-13.2 dB** | **0.594** | fp32 316.4MB / fp16 165.3MB | ✅ **效果首选（用户听感验收"效果非常好"）** |
| UVR-MDX-NET-Inst_HQ_3 | -14.18 dB | -25.52 dB | -12.44 dB | 1.18 | 63.7MB | ✅ 有效（次优效果、最易部署） |
| UVR-BVE-4B (a5) | -22.33 dB | -14.93 dB | -1.85 dB | ~1.5 | 213.3MB | ❌ 疑似 vocals/instrumental 轨互换，无效 |
| UVR-BVE-4B (a15) | -22.11 dB | -15.02 dB | -1.94 dB | 1.47 | 213.3MB | ❌ 与 a5 一致无效（aggression 不敏感） |
| Spleeter 2-stems | -26.2 dB | -12.1 dB | ≈0 | 0.24 | ~40MB | ❌ 语音/朗诵类人声严重遗漏 |

**关键判读**：
- BGM 抑制 = 伴奏被压低的量（越大越好）。Demucs 与 MDX 都能把伴奏从 -13dB 级压到 -25dB 级（12-13dB 抑制）。
- VR 架构（UVR-BVE-4B）在朗诵/对白类素材上失效（人声轨比伴奏轨低 7dB），aggression 5→15 无改善——VR 模型更适合歌曲类。
- Spleeter 对语音类人声遗漏（人声轨 -26dB 几乎无声）。

## 2. 分档输出（processed_g0.0 ~ g1.0，RMS dB）

| 方案 | g0.0 | g0.1 | g0.3 | g0.5 | g1.0 |
|---|---|---|---|---|---|
| Demucs htdemucs | -13.5 | -13.0 | -12.1 | -11.1 | -8.4 |
| UVR-MDX-Inst_HQ_3 | -14.18 | -13.54 | -12.44 | -11.21 | -7.89 |
| UVR-BVE-4B a15 | -22.11 | -21.46 | -19.58 | -17.6 | -13.53 |

（g 为伴奏混入比例，g=0 纯人声，g=1 原声）

## 3. htdemucs fp16 ONNX：NaN 根因定论（QNN HTP 前置）

| 项 | fp32 | fp16 转换版 |
|---|---|---|
| 体积 | 316.4 MB | 165.3 MB（-52.2%） |
| ORT 加载 | ✅ | ✅（加载可通过） |
| **推理输出** | ✅ 正常 | ❌ **输出全 NaN（2751840/2751840）** |
| CPU 推理 | 5121ms | 5860~7213ms |

**NaN 根因（已逐层探针定位铁证）**：
1. **模型开头样本标准差归一化**：`Cast_3` 把输入长度常量 **343980 转 fp16 → 溢出为 inf**（fp16 最大 65504）→ `Sub_4 = inf`、`Mul_4 = inf` → `Div_2 = inf/inf = NaN` → `Sqrt=NaN` → 归一化输出全 NaN → 全图 NaN。
2. 修复验证（fix_norm_chain_v5.py 把统计链抬 fp32）：开头 Div_3/tencoder.0 恢复干净，但 **encoder.0 出现第二处 fp16 溢出**（密集探针 105 点、72 个 NaN，首坏点 /encoder.0/conv/Conv）。
3. **结论**：fp16 转换存在**多处数值溢出点**，逐一修复成本高；且 **ORT QNN EP 不支持 GRU**（htdemucs 含 GRU，HTP 加载 exit 134 根因之一）、HTP 对非量化模型支持有限——**Demucs 的 HTP 部署路线整体受阻**。

## 4. canoe SM8850 实机三后端实测（unified_bench，随机输入 repeat=10）

| 模型 | ONNX_CPU | ONNX_QNN_CPU | **ONNX_QNN_HTP** |
|---|---|---|---|
| **UVR-MDX-NET-Inst_HQ_3**（fp32 现成） | 4075ms | 3361ms（1.29x） | **113.7ms（48.7x）✅** |
| Demucs htdemucs（fp32） | 4675ms | 2924ms（唯一可用） | exit 134 ❌ |
| Demucs htdemucs（fp16 转换） | 3474ms | 类型错（Gelu fp16） | 未测（NaN 已否决） |

- UVR-MDX HTP 详情：10 次 run 稳定 112~116ms，max_diff=0.140（fp16 中间精度，音频可接受），峰值内存 174.8MB。
- 报告：`docs\uvr_mdx_bench_verification.md`；原始日志：`docs\uvr_bench_self.log`。

## 5. 推荐方案（最终排序）

1. **部署算法：UVR-MDX-NET-Inst_HQ_3 + ORT QNN HTP**——实机 113.7ms/48.7x，现成 fp32 模型免转换免量化，绕过 Demucs 全部阻塞点；效果次优（-12.44dB 抑制）但听感有效。
2. **效果基准：Demucs htdemucs**——用户听感验收方案；HTP 部署受 GRU 不支持 + fp16 NaN 双重阻塞，仅保留 PC/CPU 形态（QNN_CPU 2924ms 可用）。
3. **已淘汰**：UVR-BVE-4B（VR 轨互换）、Spleeter 2-stems（人声遗漏）。
4. **下一步**：App 接入 MDX-Net 频谱前后处理（复用 SpleeterProcessor STFT 框架）+ HTP session 集成（per-session 静音已通）。

## 6. 产物位置
- 分离输出: `docs/demo_audio/separation_results/{demucs,uvr_mdx}`
- fp16 ONNX（NaN 版，仅存档）: `onnx_export/htdemucs_fp16_ok.onnx`；修复脚本 `fix_norm_chain_v5.py`
- UVR 实机报告: `docs/uvr_mdx_bench_verification.md`；日志 `docs/uvr_bench_self.log`
- 频谱图: `demucs/yimeng/spectrogram_htdemucs.png`, `uvr_mdx/vr_bve4b_a15/spectrogram_vr_bve4b_a15.png`
