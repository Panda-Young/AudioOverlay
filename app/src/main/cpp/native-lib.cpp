#include <jni.h>
#include <android/log.h>
#include "BgmProcessor.h"

#define TAG "AudioRouteLab"
#define LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

// Singleton processor instance
static BgmProcessor* g_processor = nullptr;
static int g_sampleRate = 48000;
static int g_channels = 2;
// 待应用的模式：在 processor 未初始化时存储 setMode 的值，
// processor 创建后自动应用，避免首次帧使用默认模式
static int g_pendingMode = 0;

extern "C" JNIEXPORT jint JNICALL
Java_com_example_audiooverlay_NativeBgmProcessor_processFrame(
        JNIEnv* env,
        jobject /* thiz */,
        jshortArray inputArray,
        jshortArray outputArray,
        jint numFrames,
        jint sampleRate,
        jint channels,
        jfloat bgmGain) {

    if (inputArray == nullptr || outputArray == nullptr) {
        LOGE("processFrame: null array");
        return -1;
    }

    // Lazy-init or re-init processor if sampleRate/channels changed
    if (g_processor == nullptr || g_sampleRate != sampleRate || g_channels != channels) {
        if (g_processor != nullptr) {
            delete g_processor;
            g_processor = nullptr;
        }
        g_sampleRate = sampleRate;
        g_channels = channels;
        g_processor = new BgmProcessor(sampleRate, channels);
        // 应用待处理的模式（用户可能在 capture 开始前就设置了模式）
        if (g_pendingMode != 0) {
            g_processor->setMode(g_pendingMode);
        }
        LOGD("processFrame: (re)initialized BgmProcessor sr=%d ch=%d mode=%d",
             sampleRate, channels, g_processor->getMode());
    }

    // Use GetShortArrayElements / ReleaseShortArrayElements
    // isCopy flag: if JNI_TRUE, data was copied; we must write back with mode 0
    jboolean isCopyInput = JNI_FALSE;
    jboolean isCopyOutput = JNI_FALSE;

    jshort* inputPtr = env->GetShortArrayElements(inputArray, &isCopyInput);
    jshort* outputPtr = env->GetShortArrayElements(outputArray, &isCopyOutput);

    if (inputPtr == nullptr || outputPtr == nullptr) {
        LOGE("processFrame: failed to get array elements");
        if (inputPtr != nullptr) env->ReleaseShortArrayElements(inputArray, inputPtr, JNI_ABORT);
        if (outputPtr != nullptr) env->ReleaseShortArrayElements(outputArray, outputPtr, JNI_ABORT);
        return -2;
    }

    int result = g_processor->process(
            reinterpret_cast<const int16_t*>(inputPtr),
            reinterpret_cast<int16_t*>(outputPtr),
            numFrames,
            bgmGain);

    // Release: mode 0 copies back and frees buffer; JNI_ABORT frees without copy
    // For input we don't need to copy back, use JNI_ABORT
    env->ReleaseShortArrayElements(inputArray, inputPtr, JNI_ABORT);
    // For output we need to copy back if it was a copy
    env->ReleaseShortArrayElements(outputArray, outputPtr, 0);

    return result;
}

extern "C" JNIEXPORT void JNICALL
Java_com_example_audiooverlay_NativeBgmProcessor_setBgmGain(
        JNIEnv* /* env */,
        jobject /* thiz */,
        jfloat gain) {
    if (g_processor != nullptr) {
        g_processor->setBgmGain(gain);
    }
}

extern "C" JNIEXPORT jfloat JNICALL
Java_com_example_audiooverlay_NativeBgmProcessor_getBgmGain(
        JNIEnv* /* env */,
        jobject /* thiz */) {
    if (g_processor != nullptr) {
        return g_processor->getBgmGain();
    }
    return 1.0f;
}

// 设置 BGM 处理模式（0=全局增益, 1=中置声道消除, 2=自适应谱滤波, 3=神经分离）
extern "C" JNIEXPORT void JNICALL
Java_com_example_audiooverlay_NativeBgmProcessor_setMode(
        JNIEnv* /* env */,
        jobject /* thiz */,
        jint mode) {
    // 始终记录待应用模式，即使 processor 尚未初始化
    g_pendingMode = static_cast<int>(mode);
    if (g_processor != nullptr) {
        g_processor->setMode(static_cast<int>(mode));
    } else {
        LOGD("NativeBgmProcessor::setMode: processor not initialized yet, mode=%d stored as pending", mode);
    }
}

// 获取当前 BGM 处理模式
extern "C" JNIEXPORT jint JNICALL
Java_com_example_audiooverlay_NativeBgmProcessor_getMode(
        JNIEnv* /* env */,
        jobject /* thiz */) {
    if (g_processor != nullptr) {
        return g_processor->getMode();
    }
    return 0; // 默认 GLOBAL_GAIN
}

extern "C" JNIEXPORT void JNICALL
Java_com_example_audiooverlay_NativeBgmProcessor_release(
        JNIEnv* /* env */,
        jobject /* thiz */) {
    if (g_processor != nullptr) {
        delete g_processor;
        g_processor = nullptr;
        LOGD("NativeBgmProcessor: released BgmProcessor");
    }
}
