#include "BgmProcessor.h"
#include <android/log.h>
#include <cmath>
#include <algorithm>
#include <cstring>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define TAG "AudioRouteLab"
#define LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

// ============================================================================
// 构造 / 析构
// ============================================================================

BgmProcessor::BgmProcessor(int sampleRate, int channels)
    : m_sampleRate(sampleRate)
    , m_channels(channels)
    , m_bgmGain(1.0f)
    , m_mode(GLOBAL_GAIN)
    , m_ringWritePos(0)
    , m_samplesSinceFrame(0)
    , m_olaWritePos(0)
    , m_olaReadPos(0)
    , m_alpha(0.5f) {
    LOGD("BgmProcessor constructed: sampleRate=%d, channels=%d, mode=GLOBAL_GAIN",
         sampleRate, channels);
    // 预分配 STFT 资源（构造时即分配，避免处理中分配导致卡顿）
    initSpectral();
}

BgmProcessor::~BgmProcessor() {
    // STFT 缓冲由 std::vector 自动释放，无需手动 delete
    LOGD("BgmProcessor destroyed");
}

// ============================================================================
// 模式 / 增益 设置与查询
// ============================================================================

void BgmProcessor::setMode(int mode) {
    if (mode < GLOBAL_GAIN || mode > NEURAL_SEPARATION) {
        LOGE("BgmProcessor::setMode invalid mode=%d, clamp to GLOBAL_GAIN", mode);
        mode = GLOBAL_GAIN;
    }
    if (m_mode != mode) {
        LOGD("BgmProcessor::setMode %d -> %d", m_mode, mode);
        m_mode = mode;
        // 切换到谱滤波模式时重置 STFT 缓冲，避免旧模式残留数据
        if (mode == ADAPTIVE_SPECTRAL) {
            resetSpectralBuffers();
        }
    }
}

int BgmProcessor::getMode() const {
    return m_mode;
}

void BgmProcessor::setBgmGain(float gain) {
    if (gain < 0.0f) gain = 0.0f;
    if (gain > 1.0f) gain = 1.0f;
    m_bgmGain = gain;
    LOGD("BgmProcessor::setBgmGain = %.3f", m_bgmGain);
}

float BgmProcessor::getBgmGain() const {
    return m_bgmGain;
}

// ============================================================================
// process 主入口：根据当前模式分发
// ============================================================================

int BgmProcessor::process(const int16_t* input, int16_t* output,
                           int numFrames, float bgmGain) {
    if (input == nullptr || output == nullptr || numFrames <= 0) {
        LOGE("BgmProcessor::process invalid args: input=%p, output=%p, numFrames=%d",
             input, output, numFrames);
        return -1;
    }

    // Clamp bgmGain to [0.0, 1.0]
    float gain = bgmGain;
    if (gain < 0.0f) gain = 0.0f;
    if (gain > 1.0f) gain = 1.0f;

    // 读取当前模式到局部变量（避免处理中途模式变化导致不一致）
    int mode = m_mode;

    switch (mode) {
        case CENTER_CHANNEL:
            processCenterChannel(input, output, numFrames, gain);
            break;
        case ADAPTIVE_SPECTRAL:
            processAdaptiveSpectral(input, output, numFrames, gain);
            break;
        case NEURAL_SEPARATION:
            // 神经分离模式不可用，降级为全局增益（详见 processNeuralSeparation 注释）
            processNeuralSeparation(input, output, numFrames, gain);
            break;
        case GLOBAL_GAIN:
        default:
            processGlobalGain(input, output, numFrames, gain);
            break;
    }

    return 0;
}

// ============================================================================
// 模式 0: GLOBAL_GAIN — 全局增益（baseline）
// 所有样本 × bgmGain，作为对照基线
// ============================================================================

void BgmProcessor::processGlobalGain(const int16_t* input, int16_t* output,
                                      int numFrames, float gain) {
    int totalSamples = numFrames * m_channels;
    for (int i = 0; i < totalSamples; i++) {
        float sample = static_cast<float>(input[i]) * gain;
        // Clamp to int16 range to avoid overflow / 爆音
        if (sample > 32767.0f) sample = 32767.0f;
        if (sample < -32768.0f) sample = -32768.0f;
        output[i] = static_cast<int16_t>(sample);
    }
}

// ============================================================================
// 模式 1: CENTER_CHANNEL — 中置声道消除（经典卡拉OK法）
//
// 原理：立体声 mid-side 分解
//   mid  = (L + R) / 2   —— 中置声道（通常为人声，因为人声通常居中混音）
//   side = (L - R) / 2   —— 侧边声道（通常为 BGM/乐器，因为伴奏有立体声展宽）
// 对 side 应用 bgmGain：side_adj = side × bgmGain
// 重建：L_out = mid + side_adj, R_out = mid - side_adj
//
// 效果：
//   bgmGain=0 → 只保留 mid（人声），消除 side（BGM）
//   bgmGain=1 → 原始立体声
// 单声道输入时退化为全局增益
// ============================================================================

void BgmProcessor::processCenterChannel(const int16_t* input, int16_t* output,
                                         int numFrames, float gain) {
    // 单声道退化为全局增益（mid-side 需要立体声）
    if (m_channels < 2) {
        processGlobalGain(input, output, numFrames, gain);
        return;
    }

    for (int i = 0; i < numFrames; i++) {
        float L = static_cast<float>(input[i * 2]);
        float R = static_cast<float>(input[i * 2 + 1]);

        // mid-side 分解
        float mid  = (L + R) * 0.5f;
        float side = (L - R) * 0.5f;

        // 对 side（BGM 分量）应用增益
        float sideAdj = side * gain;

        // 重建立体声
        float Lout = mid + sideAdj;
        float Rout = mid - sideAdj;

        // Clamp to int16
        if (Lout > 32767.0f) Lout = 32767.0f;
        if (Lout < -32768.0f) Lout = -32768.0f;
        if (Rout > 32767.0f) Rout = 32767.0f;
        if (Rout < -32768.0f) Rout = -32768.0f;

        output[i * 2]     = static_cast<int16_t>(Lout);
        output[i * 2 + 1] = static_cast<int16_t>(Rout);
    }
}

// ============================================================================
// 模式 2: ADAPTIVE_SPECTRAL — 自适应谱滤波
//
// 原理：在 STFT 频域对每个频率 bin 独立做 mid-side 分解，
// 根据每个 bin 的中置/侧边能量比自适应调整 side 增益。
// 比模式 1 更精细：人声频段（中置能量高）少衰减 BGM，
// BGM 频段（侧边能量高）多衰减。
//
// 参数：帧大小 1024，hop 256（75% 重叠），Hann 窗
// 增益公式：gain_bin = bgmGain × (1 - α × center_ratio)
//   center_ratio = mid_energy / (mid_energy + side_energy)
//   α = 0.5（中置能量比敏感度）
// ============================================================================

void BgmProcessor::initSpectral() {
    // 预分配所有缓冲（构造时一次性分配，避免实时处理中 malloc）
    m_hannWindow.resize(FFT_SIZE);
    m_inputRingL.resize(FFT_SIZE, 0.0f);
    m_inputRingR.resize(FFT_SIZE, 0.0f);
    m_olaL.resize(OLA_SIZE, 0.0f);
    m_olaR.resize(OLA_SIZE, 0.0f);
    m_fftRealL.resize(FFT_SIZE, 0.0f);
    m_fftImagL.resize(FFT_SIZE, 0.0f);
    m_fftRealR.resize(FFT_SIZE, 0.0f);
    m_fftImagR.resize(FFT_SIZE, 0.0f);

    // 预计算 Hann 窗：w[n] = 0.5 * (1 - cos(2πn/(N-1)))
    for (int i = 0; i < FFT_SIZE; i++) {
        m_hannWindow[i] = 0.5f * (1.0f - cosf(2.0f * static_cast<float>(M_PI) * i / (FFT_SIZE - 1)));
    }

    resetSpectralBuffers();
    LOGD("BgmProcessor: spectral resources allocated (FFT=%d, HOP=%d, OLA=%d)",
         FFT_SIZE, HOP_SIZE, OLA_SIZE);
}

void BgmProcessor::resetSpectralBuffers() {
    // 清零环形缓冲和 OLA 缓冲
    std::fill(m_inputRingL.begin(), m_inputRingL.end(), 0.0f);
    std::fill(m_inputRingR.begin(), m_inputRingR.end(), 0.0f);
    std::fill(m_olaL.begin(), m_olaL.end(), 0.0f);
    std::fill(m_olaR.begin(), m_olaR.end(), 0.0f);
    m_ringWritePos = 0;
    m_samplesSinceFrame = 0;
    m_olaWritePos = 0;
    m_olaReadPos = 0;
}

// ============================================================================
// 自包含 radix-2 Cooley-Tukey FFT（原地运算）
// 不依赖任何外部库，纯 C++ 实现。
// n 必须为 2 的幂。
// inverse=false: 正变换 X[k] = Σ x[n]·e^(-j2πkn/N)（无缩放）
// inverse=true:  逆变换 x[n] = (1/N)·Σ X[k]·e^(j2πkn/N)（1/N 缩放）
// ============================================================================

void BgmProcessor::fft(float* real, float* imag, int n, bool inverse) {
    // --- 第一步：位逆序置换（Bit-reversal permutation） ---
    // 将输入重新排列为 FFT 所需的位逆序
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) {
            j ^= bit;
        }
        j ^= bit;
        if (i < j) {
            std::swap(real[i], real[j]);
            std::swap(imag[i], imag[j]);
        }
    }

    // --- 第二步：Cooley-Tukey 蝶形运算 ---
    // 从长度 2 开始，逐层合并到长度 n
    for (int len = 2; len <= n; len <<= 1) {
        // 当前层的旋转因子角度
        float ang = 2.0f * static_cast<float>(M_PI) / len * (inverse ? -1.0f : 1.0f);
        float wlenReal = cosf(ang);
        float wlenImag = sinf(ang);

        for (int i = 0; i < n; i += len) {
            float wReal = 1.0f;
            float wImag = 0.0f;
            for (int j = 0; j < len / 2; j++) {
                // 蝶形运算：u = 上半部分, v = 下半部分 × 旋转因子
                float uReal = real[i + j];
                float uImag = imag[i + j];
                float vReal = real[i + j + len / 2] * wReal
                            - imag[i + j + len / 2] * wImag;
                float vImag = real[i + j + len / 2] * wImag
                            + imag[i + j + len / 2] * wReal;

                real[i + j]             = uReal + vReal;
                imag[i + j]             = uImag + vImag;
                real[i + j + len / 2]   = uReal - vReal;
                imag[i + j + len / 2]   = uImag - vImag;

                // 更新旋转因子 w = w × wlen
                float newWReal = wReal * wlenReal - wImag * wlenImag;
                wImag = wReal * wlenImag + wImag * wlenReal;
                wReal = newWReal;
            }
        }
    }

    // --- 逆变换缩放（1/N） ---
    if (inverse) {
        for (int i = 0; i < n; i++) {
            real[i] /= n;
            imag[i] /= n;
        }
    }
}

// ============================================================================
// 处理一帧 STFT：
//   1. 从环形缓冲读取最近 FFT_SIZE 个样本
//   2. 应用 Hann 窗
//   3. FFT 变换到频域
//   4. 每个 bin 做 mid-side 分解，计算能量比，应用自适应增益
//   5. 重建 L/R 频域
//   6. IFFT 变回时域
//   7. 叠加到 OLA 输出缓冲
// ============================================================================

void BgmProcessor::processOneSpectralFrame(float bgmGain) {
    // --- 1. 从环形缓冲读取最近 FFT_SIZE 个样本并加窗 ---
    // 环形缓冲中最近的 FFT_SIZE 样本起始位置 = (writePos - FFT_SIZE) mod FFT_SIZE
    int ringStart = (m_ringWritePos - FFT_SIZE + FFT_SIZE) % FFT_SIZE;

    for (int i = 0; i < FFT_SIZE; i++) {
        int idx = (ringStart + i) % FFT_SIZE;
        m_fftRealL[i] = m_inputRingL[idx] * m_hannWindow[i];
        m_fftImagL[i] = 0.0f;
        m_fftRealR[i] = m_inputRingR[idx] * m_hannWindow[i];
        m_fftImagR[i] = 0.0f;
    }

    // --- 2. FFT 正变换 ---
    fft(m_fftRealL.data(), m_fftImagL.data(), FFT_SIZE, false);
    fft(m_fftRealR.data(), m_fftImagR.data(), FFT_SIZE, false);

    // --- 3. 每个 bin 做 mid-side 分解 + 自适应增益 ---
    // 只需要处理正频率 bin（0 到 FFT_SIZE/2），负频率为共轭对称
    // 但为了简化，直接处理所有 bin（共轭对会获得相同增益，保持对称性）
    for (int k = 0; k < FFT_SIZE; k++) {
        float Lr = m_fftRealL[k];
        float Li = m_fftImagL[k];
        float Rr = m_fftRealR[k];
        float Ri = m_fftImagR[k];

        // mid-side 分解（频域）
        float midR  = (Lr + Rr) * 0.5f;
        float midI  = (Li + Ri) * 0.5f;
        float sideR = (Lr - Rr) * 0.5f;
        float sideI = (Li - Ri) * 0.5f;

        // 计算 mid / side 能量
        float midEnergy  = midR * midR + midI * midI;
        float sideEnergy = sideR * sideR + sideI * sideI;
        float totalEnergy = midEnergy + sideEnergy;

        // 中置能量占比 center_ratio = mid_energy / total_energy
        // center_ratio 接近 1 → 该 bin 主要是人声（中置）
        // center_ratio 接近 0 → 该 bin 主要是 BGM（侧边）
        float centerRatio = 0.0f;
        if (totalEnergy > 1e-12f) {
            centerRatio = midEnergy / totalEnergy;
        }

        // 自适应增益公式：gain_bin = bgmGain × (1 - α × center_ratio)
        // 中置能量高的 bin：side 本身较少，增益较低（进一步压制残留 BGM 泄漏）
        // 侧边能量高的 bin：side 较多，增益接近 bgmGain（按用户设定衰减 BGM）
        float gainBin = bgmGain * (1.0f - m_alpha * centerRatio);
        if (gainBin < 0.0f) gainBin = 0.0f;
        if (gainBin > 1.0f) gainBin = 1.0f;

        // 对 side 分量应用自适应增益
        float sideAdjR = sideR * gainBin;
        float sideAdjI = sideI * gainBin;

        // 重建 L/R 频域
        m_fftRealL[k] = midR + sideAdjR;
        m_fftImagL[k] = midI + sideAdjI;
        m_fftRealR[k] = midR - sideAdjR;
        m_fftImagR[k] = midI - sideAdjI;
    }

    // --- 4. IFFT 逆变换变回时域 ---
    fft(m_fftRealL.data(), m_fftImagL.data(), FFT_SIZE, true);
    fft(m_fftRealR.data(), m_fftImagR.data(), FFT_SIZE, true);

    // --- 5. 叠加到 OLA 输出缓冲 ---
    // Hann 窗在 75% 重叠下满足 COLA 条件（Σw[n] = 2），
    // 因此 OLA 输出幅度为原始的 2 倍，读取时除以 2 归一化。
    for (int i = 0; i < FFT_SIZE; i++) {
        int olaIdx = (m_olaWritePos + i) % OLA_SIZE;
        m_olaL[olaIdx] += m_fftRealL[i];
        m_olaR[olaIdx] += m_fftRealR[i];
    }

    // OLA 写指针前进一个 hop
    m_olaWritePos = (m_olaWritePos + HOP_SIZE) % OLA_SIZE;
}

// ============================================================================
// 模式 2 主处理入口（流式输入）
//
// 流式处理流程：
//   1. 将输入样本解交织后写入环形缓冲
//   2. 每积累 HOP_SIZE 个新样本，处理一帧 STFT（产生 HOP_SIZE 输出）
//   3. 从 OLA 缓冲读取尽可能多的输出样本
//   4. 若 OLA 中数据不足（初始延迟期），剩余样本用原始输入直通
//
// 注意：numFrames 不一定是 HOP_SIZE 的整数倍，剩余样本会缓存在环形缓冲中，
//       与下一次调用的样本合并处理。
// ============================================================================

void BgmProcessor::processAdaptiveSpectral(const int16_t* input, int16_t* output,
                                             int numFrames, float gain) {
    // 单声道退化为全局增益（mid-side 需要立体声）
    if (m_channels < 2) {
        processGlobalGain(input, output, numFrames, gain);
        return;
    }

    // --- 1. 将输入样本写入环形缓冲，并在积累足够样本时处理 STFT 帧 ---
    for (int i = 0; i < numFrames; i++) {
        // 解交织写入环形缓冲
        m_inputRingL[m_ringWritePos] = static_cast<float>(input[i * 2]);
        m_inputRingR[m_ringWritePos] = static_cast<float>(input[i * 2 + 1]);
        m_ringWritePos = (m_ringWritePos + 1) % FFT_SIZE;
        m_samplesSinceFrame++;

        // 每积累 HOP_SIZE 个新样本，处理一帧 STFT
        if (m_samplesSinceFrame >= HOP_SIZE) {
            m_samplesSinceFrame -= HOP_SIZE;
            processOneSpectralFrame(gain);
        }
    }

    // --- 2. 从 OLA 缓冲读取输出 ---
    // 计算 OLA 中可用的样本数（写指针与读指针之间的距离）
    int available = (m_olaWritePos - m_olaReadPos + OLA_SIZE) % OLA_SIZE;
    int toRead = std::min(numFrames, available);

    for (int i = 0; i < toRead; i++) {
        // Hann 窗 75% 重叠 COLA 增益为 2，除以 2 归一化
        float L = m_olaL[m_olaReadPos] * 0.5f;
        float R = m_olaR[m_olaReadPos] * 0.5f;

        // 读取后清零该位置（避免旧数据残留影响后续重叠）
        m_olaL[m_olaReadPos] = 0.0f;
        m_olaR[m_olaReadPos] = 0.0f;

        m_olaReadPos = (m_olaReadPos + 1) % OLA_SIZE;

        // Clamp to int16
        if (L > 32767.0f) L = 32767.0f;
        if (L < -32768.0f) L = -32768.0f;
        if (R > 32767.0f) R = 32767.0f;
        if (R < -32768.0f) R = -32768.0f;

        output[i * 2]     = static_cast<int16_t>(L);
        output[i * 2 + 1] = static_cast<int16_t>(R);
    }

    // --- 3. 初始延迟期：OLA 数据不足时，剩余样本用原始输入直通 ---
    // （前约 FFT_SIZE-HOP_SIZE = 768 样本 ≈ 16ms 为 STFT 填充期）
    for (int i = toRead; i < numFrames; i++) {
        output[i * 2]     = input[i * 2];
        output[i * 2 + 1] = input[i * 2 + 1];
    }
}

// ============================================================================
// 模式 3: NEURAL_SEPARATION — 神经分离（NOT_AVAILABLE）
//
// ============================================================================
// 瓶颈分析（为什么不在移动端实时集成 Spleeter / ONNX Runtime）：
//
// 1. 模型体积：
//    - Spleeter 2-stem（vocals/accompaniment）原始模型约 100MB+
//    - 即使 INT8 量化后仍约 25~50MB，加上 ONNX Runtime Mobile 运行时
//      （每 ABI 约 10~15MB），APK 体积增加 40~70MB，超出 <50MB 约束
//
// 2. 推理延迟：
//    - Spleeter 使用 4096-sample STFT + U-Net 架构（约 10M 参数）
//    - 在移动端 ARM64 CPU 上，单次推理（一帧 4096 样本）约 50~200ms
//    - 实时音频要求：48kHz / 256-hop = 5.3ms/帧，延迟超标 10~40 倍
//    - 即使批处理/跳帧，端到端延迟也 > 500ms，无法用于实时通话/直播
//
// 3. CPU 占用与功耗：
//    - 持续推理 CPU 占用 > 80%（旗舰 SoC），中端设备直接卡顿
//    - 导致设备发热、降频、音频缓冲区 underrun（爆音/断音）
//    - 电池消耗显著增加，不适合长时间运行
//
// 4. 替代方案评估：
//    - Demucs（Transformer 架构）：更重，完全不可行
//    - MDX-Net 轻量版：约 5~10M 参数，量化后 10~20MB，延迟 20~50ms/帧
//      仍超标 4~10 倍，且需要 NNAPI / GPU 委托才能接近实时
//    - 移动端实时 BGM 分离的工业界方案：通常用模式 1/2 这类传统 DSP，
//      或在云端做分离（端云协同），端侧神经分离目前仍不实用
//
// 结论：NEURAL_SEPARATION 模式在当前移动端硬件条件下不可行，
//       保留枚举但降级为 GLOBAL_GAIN，并在日志中提示。
//       未来若出现 <5ms/帧的轻量模型 + NNAPI 加速，可在此处接入。
// ============================================================================

void BgmProcessor::processNeuralSeparation(const int16_t* input, int16_t* output,
                                             int numFrames, float gain) {
    // 神经分离不可用，降级为全局增益
    // 仅在首次调用时打印一次提示（通过静态变量控制）
    static bool s_warned = false;
    if (!s_warned) {
        LOGE("BgmProcessor: NEURAL_SEPARATION mode NOT_AVAILABLE on mobile, "
             "fallback to GLOBAL_GAIN. See bottleneck analysis in BgmProcessor.cpp");
        s_warned = true;
    }
    processGlobalGain(input, output, numFrames, gain);
}
