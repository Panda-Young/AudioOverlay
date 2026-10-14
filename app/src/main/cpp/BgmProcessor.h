#ifndef BGMPROCESSOR_H
#define BGMPROCESSOR_H

#include <cstdint>
#include <vector>

/**
 * BGM 处理器：支持多种可切换的 BGM 处理算法模式。
 *
 * 模式 0 GLOBAL_GAIN:      全局增益（baseline，所有样本 × bgmGain）
 * 模式 1 CENTER_CHANNEL:   中置声道消除（mid-side 分解，经典卡拉OK法，零延迟）
 * 模式 2 ADAPTIVE_SPECTRAL: 自适应谱滤波（STFT 频域 per-bin mid-side，更精细）
 * 模式 3 NEURAL_SEPARATION: 神经分离（NOT_AVAILABLE，详见 cpp 中瓶颈分析注释）
 */
class BgmProcessor {
public:
    // BGM 处理模式枚举
    enum BgmMode {
        GLOBAL_GAIN       = 0,  // 全局增益（baseline）
        CENTER_CHANNEL    = 1,  // 中置声道消除（mid-side 卡拉OK法）
        ADAPTIVE_SPECTRAL = 2,  // 自适应谱滤波（STFT 频域中置分离）
        NEURAL_SEPARATION = 3   // 神经分离（NOT_AVAILABLE，降级为全局增益）
    };

    BgmProcessor(int sampleRate, int channels);
    ~BgmProcessor();

    // 处理一帧 PCM 数据（interleaved stereo）
    // numFrames: 帧数（每帧 = channels 个 sample）
    // bgmGain:   BGM 增益 0.0~1.0（0=消除, 1=原始）
    // 返回: 0 成功, 非0 错误码
    int process(const int16_t* input, int16_t* output, int numFrames, float bgmGain);

    void setBgmGain(float gain);
    float getBgmGain() const;

    // 设置/获取当前处理模式
    void setMode(int mode);
    int  getMode() const;

private:
    // === 通用成员 ===
    int   m_sampleRate;
    int   m_channels;
    float m_bgmGain;
    int   m_mode;  // 当前处理模式（BgmMode 枚举值）

    // === 模式 0: 全局增益 ===
    void processGlobalGain(const int16_t* input, int16_t* output,
                           int numFrames, float gain);

    // === 模式 1: 中置声道消除（mid-side） ===
    void processCenterChannel(const int16_t* input, int16_t* output,
                              int numFrames, float gain);

    // === 模式 2: 自适应谱滤波（STFT） ===
    static const int FFT_SIZE  = 1024;  // FFT 帧大小
    static const int HOP_SIZE  = 256;   // 帧移（75% 重叠）
    static const int NUM_BINS  = FFT_SIZE / 2 + 1;  // 正频率 bin 数
    static const int OLA_SIZE  = FFT_SIZE * 8;       // 重叠相加缓冲大小（足够容纳多帧重叠）

    std::vector<float> m_hannWindow;  // Hann 窗函数（分析用）

    // 输入环形缓冲（保存最近 FFT_SIZE 个样本，用于 STFT 加窗）
    std::vector<float> m_inputRingL;  // 左声道环形缓冲
    std::vector<float> m_inputRingR;  // 右声道环形缓冲
    int   m_ringWritePos;              // 环形缓冲写指针
    int   m_samplesSinceFrame;         // 自上次 STFT 帧处理以来新增的样本数

    // 输出重叠相加（Overlap-Add）缓冲
    std::vector<float> m_olaL;         // 左声道 OLA 缓冲
    std::vector<float> m_olaR;         // 右声道 OLA 缓冲
    int   m_olaWritePos;               // OLA 写指针
    int   m_olaReadPos;                // OLA 读指针

    // FFT 临时缓冲（避免每帧分配）
    std::vector<float> m_fftRealL;
    std::vector<float> m_fftImagL;
    std::vector<float> m_fftRealR;
    std::vector<float> m_fftImagR;

    // 自适应增益参数：中置能量比敏感度
    // gain_bin = bgmGain * (1 - alpha * center_ratio)
    float m_alpha;

    // 初始化/重置 STFT 相关资源
    void initSpectral();
    void resetSpectralBuffers();

    // 自包含 radix-2 Cooley-Tukey FFT（原地运算，n 必须为 2 的幂）
    // inverse=false 正变换（无缩放），inverse=true 逆变换（1/n 缩放）
    static void fft(float* real, float* imag, int n, bool inverse);

    // 处理一帧 STFT：从环形缓冲取 FFT_SIZE 样本 → 加窗 → FFT → per-bin 处理 → IFFT → 叠加到 OLA
    void processOneSpectralFrame(float bgmGain);

    // 模式 2 主处理入口（流式输入，处理 numFrames 个样本）
    void processAdaptiveSpectral(const int16_t* input, int16_t* output,
                                  int numFrames, float gain);

    // === 模式 3: 神经分离（NOT_AVAILABLE，降级处理） ===
    void processNeuralSeparation(const int16_t* input, int16_t* output,
                                  int numFrames, float gain);
};

#endif // BGMPROCESSOR_H
