/*
 * MDX-Net 频谱预处理/后处理（FFTW3 实现，float 单精度）
 *
 * 参数与 audio-separator MDXSeparator 完全一致（PC 侧已用 torch 复刻验证 corr=1.000）：
 *   n_fft = 6144, hop = 1024, dim_f = 3072, hann periodic, center=True
 *   模型输入/输出 [1,4,3072,256]：4 通道 = [L实, L虚, R实, R虚], freq=3072, time=256
 *
 * STFT 布局（out4 / in4）：
 *   index = ((ch*2 + reim) * DIM_F + k) * nFrames + t
 *   ch=0/1, reim=0/1, k in [0,3072), t in [0,nFrames)
 */
#include <jni.h>
#include <cmath>
#include <vector>
#include <cstring>
#include <android/log.h>
#include <fftw3.h>

#define MDX_TAG "MdxStft"
#define MDX_LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, MDX_TAG, __VA_ARGS__)
#define MDX_LOGE(...) __android_log_print(ANDROID_LOG_ERROR, MDX_TAG, __VA_ARGS__)

static const int MDX_N_FFT = 6144;
static const int MDX_HOP = 1024;
static const int MDX_DIM_F = 3072;

class MdxStftEngine {
public:
    MdxStftEngine() {
        m_hann.resize(MDX_N_FFT);
        for (int i = 0; i < MDX_N_FFT; i++) {
            // torch.hann_window(periodic=True): 0.5*(1-cos(2*pi*i/N))
            m_hann[i] = 0.5f * (1.0f - cosf(2.0f * static_cast<float>(M_PI) * i / MDX_N_FFT));
        }
        // 连续内存缓冲（fftwf_complex = float[2]，不能作 vector 元素类型）
        m_in.resize(2 * MDX_N_FFT);
        m_out.resize(2 * MDX_N_FFT);
        m_planFwd = fftwf_plan_dft_1d(MDX_N_FFT, reinterpret_cast<fftwf_complex*>(m_in.data()),
                                      reinterpret_cast<fftwf_complex*>(m_out.data()),
                                      FFTW_FORWARD, FFTW_ESTIMATE);
        m_planInv = fftwf_plan_dft_1d(MDX_N_FFT, reinterpret_cast<fftwf_complex*>(m_in.data()),
                                      reinterpret_cast<fftwf_complex*>(m_out.data()),
                                      FFTW_BACKWARD, FFTW_ESTIMATE);
        MDX_LOGD("MdxStftEngine created: n_fft=%d hop=%d dim_f=%d", MDX_N_FFT, MDX_HOP, MDX_DIM_F);
    }

    ~MdxStftEngine() {
        fftwf_destroy_plan(m_planFwd);
        fftwf_destroy_plan(m_planInv);
    }

    // stereo float (2*n) -> 4*DIM_F*T 平铺；返回帧数 T（center=True 语义）
    int stft(const float* stereo, int nSamples, float* out4) {
        const int T = nSamples / MDX_HOP + 1;
        for (int t = 0; t < T; t++) {
            const int start = t * MDX_HOP - MDX_N_FFT / 2;  // center=True
            for (int ch = 0; ch < 2; ch++) {
                for (int i = 0; i < MDX_N_FFT; i++) {
                    const int idx = start + i;
                    float v = 0.0f;
                    if (idx >= 0 && idx < nSamples) v = stereo[idx * 2 + ch] * m_hann[i];
                    m_in[i * 2] = v;
                    m_in[i * 2 + 1] = 0.0f;
                }
                fftwf_execute(m_planFwd);
                const float* buf = m_out.data();
                for (int k = 0; k < MDX_DIM_F; k++) {
                    out4[((ch * 2 + 0) * MDX_DIM_F + k) * T + t] = buf[k * 2];
                    out4[((ch * 2 + 1) * MDX_DIM_F + k) * T + t] = buf[k * 2 + 1];
                }
            }
        }
        return T;
    }

    // 4*DIM_F*T 平铺 -> stereo float (2*outLen)
    void istft(const float* in4, int nFrames, int outLen, float* stereo) {
        std::vector<float> accum(2 * outLen, 0.0f);
        std::vector<float> wsum(2 * outLen, 0.0f);
        const float invN = 1.0f / MDX_N_FFT;
        for (int t = 0; t < nFrames; t++) {
            const int start = t * MDX_HOP - MDX_N_FFT / 2;
            for (int ch = 0; ch < 2; ch++) {
                for (int k = 0; k < MDX_DIM_F; k++) {
                    m_in[k * 2] = in4[((ch * 2 + 0) * MDX_DIM_F + k) * nFrames + t];
                    m_in[k * 2 + 1] = in4[((ch * 2 + 1) * MDX_DIM_F + k) * nFrames + t];
                }
                // 其余 bin（含 Nyquist 3073）置 0
                for (int k = MDX_DIM_F; k < MDX_N_FFT; k++) {
                    m_in[k * 2] = 0.0f;
                    m_in[k * 2 + 1] = 0.0f;
                }
                fftwf_execute(m_planInv);
                const float* re = m_out.data();
                for (int i = 0; i < MDX_N_FFT; i++) {
                    const int idx = start + i;
                    if (idx >= 0 && idx < outLen) {
                        const float v = re[i * 2] * invN * m_hann[i];
                        accum[idx * 2 + ch] += v;
                        wsum[idx * 2 + ch] += m_hann[i] * m_hann[i];
                    }
                }
            }
        }
        for (int i = 0; i < 2 * outLen; i++) {
            stereo[i] = (wsum[i] > 1e-8f) ? (accum[i] / wsum[i]) : 0.0f;
        }
    }

private:
    std::vector<float> m_hann;
    std::vector<float> m_in;    // 2*N_FFT 连续缓冲（复数视图）
    std::vector<float> m_out;
    fftwf_plan m_planFwd = nullptr;
    fftwf_plan m_planInv = nullptr;
};

static MdxStftEngine* g_engine = nullptr;

static MdxStftEngine* engine() {
    if (g_engine == nullptr) g_engine = new MdxStftEngine();
    return g_engine;
}

extern "C" JNIEXPORT void JNICALL
Java_com_example_audiooverlay_MDXProcessor_nativeSetAdspLibraryPath(
        JNIEnv* env, jclass /* clazz */, jstring path) {
    if (path == nullptr) return;
    const char* p = env->GetStringUTFChars(path, nullptr);
    if (p != nullptr) {
        // QNN HTP backend locates its DSP skeleton (.so) via ADSP_LIBRARY_PATH.
        // Must be set before OrtSession create so QNN HTP can find libQnnHtpV81Skel.so.
        setenv("ADSP_LIBRARY_PATH", p, 1);
        env->ReleaseStringUTFChars(path, p);
        __android_log_print(ANDROID_LOG_INFO, "MdxStft", "ADSP_LIBRARY_PATH set to %s", p);
    }
}

extern "C" JNIEXPORT jfloatArray JNICALL
Java_com_example_audiooverlay_MDXProcessor_mdxStft(
        JNIEnv* env, jobject /* thiz */,
        jfloatArray stereo44100, jint nSamples) {
    if (stereo44100 == nullptr || nSamples <= 0) return nullptr;
    jfloat* src = env->GetFloatArrayElements(stereo44100, nullptr);
    if (src == nullptr) return nullptr;

    MdxStftEngine* e = engine();
    const int T = nSamples / MDX_HOP + 1;
    const int outLen = 4 * MDX_DIM_F * T;
    jfloatArray result = env->NewFloatArray(outLen);
    jfloat* dst = env->GetFloatArrayElements(result, nullptr);
    if (dst != nullptr) {
        e->stft(src, nSamples, dst);
        env->ReleaseFloatArrayElements(result, dst, 0);
    }
    env->ReleaseFloatArrayElements(stereo44100, src, JNI_ABORT);
    return result;
}

extern "C" JNIEXPORT jfloatArray JNICALL
Java_com_example_audiooverlay_MDXProcessor_mdxIstft(
        JNIEnv* env, jobject /* thiz */,
        jfloatArray spec4, jint nFrames, jint outLen) {
    if (spec4 == nullptr || nFrames <= 0 || outLen <= 0) return nullptr;
    jfloat* src = env->GetFloatArrayElements(spec4, nullptr);
    if (src == nullptr) return nullptr;

    MdxStftEngine* e = engine();
    jfloatArray result = env->NewFloatArray(2 * outLen);
    jfloat* dst = env->GetFloatArrayElements(result, nullptr);
    if (dst != nullptr) {
        e->istft(src, nFrames, outLen, dst);
        env->ReleaseFloatArrayElements(result, dst, 0);
    }
    env->ReleaseFloatArrayElements(spec4, src, JNI_ABORT);
    return result;
}
