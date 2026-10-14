plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "com.example.audiooverlay"
    compileSdk {
        version = release(37)
    }

    ndkVersion = "28.2.13676358"

    defaultConfig {
        applicationId = "com.example.audiooverlay"
        minSdk = 28
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            // FFTW3 prebuilt 仅 arm64-v8a（canoe/Redmi 真机均为 arm64）
            abiFilters += listOf("arm64-v8a")
        }

        externalNativeBuild {
            cmake {
                cppFlags += "-std=c++17"
                arguments += listOf("-DANDROID_STL=c++_shared")
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    buildTypes {
        release {
            optimization {
                enable = false
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
    }

    packaging {
        jniLibs {
            // 提取 .so 到 nativeLibraryDir（QNN 的 libQnnHtpV81Skel.so 需要真实文件路径，
            // 供 fastrpc/ADSP_LIBRARY_PATH 读取；默认 extractNativeLibs=false 时无法满足）
            useLegacyPackaging = true
        }
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    // ONNX Runtime QNN 版 (Android) — 官方 QNN EP 特制 AAR（Maven Central）
    // 自带 QNN 版 libonnxruntime.so + Java JNI 绑定；QNN backend 库（libQnn*.so）
    // 由 jniLibs/arm64-v8a 提供（QNN SDK v81, SM8850）。
    // 1.22.0/1.23.0 的 ParseHtpArchitecture 仅支持 68/69/73/75（无 v81，SM8850 需 v81），
    // 升级到 1.26.0（含 v79/v81 arch 支持；API addQnn(Map) 兼容）。
    implementation("com.microsoft.onnxruntime:onnxruntime-android-qnn:1.26.0")
    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
