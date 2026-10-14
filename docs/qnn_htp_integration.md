# QNN HTP 加速集成 — canoe 真机验证记录（2026-09-04 05:47 更新：filesDir 自包含方案闭环）

## 结论（一句话）

**QNN HTP 在 canoe 真机（SM8850/Hexagon v81）激活成功**：MDX-Net 单窗口推理从 CPU EP 的 **3.86s** 降至 **0.24–0.6s**（稳态 ~0.28s，**~14x 加速**），RTF≈0.06，远优于实时需求（窗口对应 4.44s 音频）。**filesDir 自包含方案（21 库 assets 打包→App 数据域解包→Enforcing 下运行）已闭环，无 root 无 permissive 依赖。**

## 方案一：设备目录方案（/data/local/tmp/qnn_app，依赖 userdebug root）

### 1. ORT 版本（决定性根因）
- 官方 qnn AAR **≤1.23.0 的 ParseHtpArchitecture 只认 68/69/73/75**（无 81）→ SM8850 v81 传 "81"/"v81" 必报 `QNN_DEVICE_ERROR_INVALID_CONFIG`。
- **1.26.0 支持 bare "81"**（源码实证：`qnn_execution_provider.cc` ParseHtpArchitecture 172-192 行）。
- Maven Central：`com.microsoft.onnxruntime:onnxruntime-android-qnn:1.26.0`（1.24.0 不存在）。

### 2. 两个自制 stub 库（绕开 App namespace 不可见的 linker 符号）
- **libdl_android_stub.c**：SONAME=libdl_android.so，导出 android_dlopen_ext/android_dlsym/android_dlvsym/android_dladdr/android_dlclose/android_dl_iterate_phdr/**android_get_exported_namespace**（返回 NULL 回退 dlopen）；仅依赖 public 的 libdl/libc。真实 libdl_android 的 NEEDED ld-android.so（linker 自身）在 App 的 clns-9 namespace 不可见，无法从文件加载。
- **libvndksupport_stub.c**：SONAME=libvndksupport.so，导出 android_load_sphal_library（dlopen 实现）/android_unload_sphal_library/android_is_in_vendor_process；仅依赖 libdl/libc。真实 libvndksupport 引用 android_get_exported_namespace 等 linker 符号，App 进程无法解析。
- 编译：NDK aarch64-linux-android21-clang，`-shared -fPIC -Wl,-soname,<原名>.so`。

### 3. 系统现场（userdebug root，重启后需重设）
```sh
setenforce 0    # SELinux permissive（App 从 shell_data_file dlopen 需要）
chmod 666 /dev/fastrpc-cdsp /dev/fastrpc-cdsp-secure /dev/fastrpc-adsp-secure /dev/fastrpc-lpass2000 /dev/fastrpc-nsp1000
setprop vendor.fastrpc.process.attrs 0   # 放开 fastrpc 进程属性检查（untrusted_app 可连 CDSP）
```

## 方案二（定案）：filesDir 自包含方案 — 无 root 无 permissive

### 原理
1. **21 个运行时库（约 120MB）打包进 APK assets/qnn/**（含 libQnnHtpPrepare.so——**必须**，见下）。
2. App 首次启动 mode5 时 `copyQnnLibsIfNeeded()`：assets → `filesDir/qnn/`，逐个校验存在+长度一致跳过。
3. MDXProcessor 从 `filesDir/qnn` **System.load 迭代式预加载 17 库** + `nativeSetAdspLibraryPath(filesDir/qnn)`。
4. **SELinux 天然允许**：App 数据域是 `app_data_file`，untrusted_app 对自有数据 dlopen/execute 有规则（实测 logcat：`avc: granted { execute } for /data/data/.../files/qnn/libc++.so`）。
5. **@ 库（vendor.qti.hardware.dsp@1.0.so）在 assets 中保持原名**：assets 不经 installd，无 '@' 剥版本问题；解包后 App 自有目录可任意命名。

### 关键坑（本轮踩到）
- **ORT 1.26 QNN EP 需要 libQnnHtpPrepare.so 存在于 ADSP_LIBRARY_PATH 目录**。缺失时 GetCapability/partitioner 行为异常：layout transformer 插入 `com.ms.internal.nhwc` 域 NHWC 节点后 partitioner 未选中 → `Node 'Conv_0_token_1' OpType:Conv with domain:com.ms.internal.nhwc ... was not selected by that EP` → graph invalid → 回退 CPU（3.5-5s/窗）。**补上 HtpPrepare（87.9MB）后 session create 直接成功**。
- **libQnnHtpV81.so / libQnnHtpV81Skel.so / libQnnHtpV81CalculatorStub.so 不预加载**（System.load 列表不含），由 QNN 经 ADSP_LIBRARY_PATH 从目录按需加载。
- **QNN 尝试用 NO_OPT**（避免 ORT 图优化插入 NHWC 节点），实测 0.24-0.3s/窗（比 ALL_OPT 0.41s 更快）；QNN 失败回退 CPU 时重建 ALL_OPT 保性能。

### 21 库清单（assets/qnn/）
libQnnHtp.so / libQnnHtpV81.so / libQnnHtpV81Stub.so / libQnnHtpV81Skel.so / libQnnHtpV81CalculatorStub.so / libQnnSystem.so / **libQnnHtpPrepare.so（必须）** / libcdsprpc.so / libhidlbase.so / libhardware.so / libutils.so / libcutils.so / libvndksupport.so(stub) / libdl_android.so(stub) / libvmmem.so / libdmabufheap.so / libbase.so / libc++.so / vendor.qti.hardware.dsp@1.0.so / vendor.qti.hardware.dsp-V1-ndk.so / android.hardware.common-V2-ndk.so

### 验证证据（2026-09-04 05:45，pm clear 干净安装，SELinux Enforcing）
```
D AudioRouteLab: QNN libs ready: dir=/data/user/0/com.example.audiooverlay/files/qnn total=21 copied=21 skipped=0
I MDXProcessor: QNN HTP EP enabled (burst/O3/fp16/vtcm8, NO_OPT)
D MDXProcessor: MDXProcessor initialized: model=/data/user/0/.../files/mdx_models/... in=input out=output   <- 无 fallback
MDX window processed 间隔：0.58s / 0.24s / 0.29s / 0.30s / 0.29s（稳态 ~0.28s）
```

## 推理耗时对比（MDX window start → processed）

| 窗口 | CPU EP（实测） | 设备目录 HTP | filesDir HTP（定案） |
|---|---|---|---|
| 0 | 3.86s | 1.374s（首次图执行） | 0.58s |
| 1 | 3.94s | 0.404s | 0.24s |
| 2 | 3.85s | 0.411s | 0.29s |
| 3 | — | 0.418s | 0.30s |
| 4 | — | 0.424s | 0.29s |
| **稳态均值** | ~3.9s | ~0.41s | **~0.28s（14x）** |

## 失败链回顾（勿重复）

1. ORT ≤1.23 传 81/v81 → INVALID_CONFIG（源码无 81 分支）。
2. vendor.qti.hardware.dsp@1.0.so 走 APK 打包（jniLibs）→ installd 按 '@' 剥版本后缀，文件名对不上 NEEDED。**assets 打包无此问题**。
3. libcdsprpc.so 改名打包 → DT_NEEDED 引用原名，改名即断。
4. enforcing 下从 /data/local/tmp dlopen → avc denied execute（untrusted_app→shell_data_file）。**filesDir（app_data_file）无此问题**。
5. 真实 libdl_android.so（NEEDED ld-android.so=linker 自身）→ App clns-9 namespace 不可见 → **自制 stub**。
6. 真实 libvndksupport.so（引用 android_get_exported_namespace 等 linker 符号）→ 无法解析 → **自制 stub**。
7. 缺 libQnnHtpPrepare.so → NHWC graph invalid → fallback CPU → **补上即通**。
8. 预加载 libQnnHtpV81Skel.so 到 AP 进程 → 曾疑干扰 CDSP（后证非根因，但保持不预加载更稳）。

## 已知限制与下一步

- **Redmi（无 root，骁龙8Gen3/v75）适配**：filesDir 方案与 root 无关——需换 v75 库（libQnnHtpV75* + CalculatorStub）进 assets；唯一待验证：MIUI 上 untrusted_app 能否打开 /dev/fastrpc-cdsp（商用机 QNN 为公开 SDK，大概率允许，需真机验证）。
- 首次 QNN 图编译 ~24s（含 HtpPrepare 后 prepare），后续会话复用快。
- 推理 0.24-0.3s vs bench 113.7ms 差距：含 STFT/ISTFT/OLA + ORT 会话开销；目标 ~300ms 级已达成。
- APK 253.5MB（assets 120MB 库 + 63.7MB MDX 模型），本地安装无碍；后续可裁（libQnnHtpV81Skel 12.6MB 等是否可去待验证）。
