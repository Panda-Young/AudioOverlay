// libvndksupport stub: 导出 SPHAL 加载 API，用普通 dlopen 语义实现。
// 目的：真实 libvndksupport.so 引用 linker 导出符号（android_get_exported_namespace 等），
// App 的 clns-9 namespace 不可见导致 dlopen 失败；此 stub 以同名 SONAME 提供
// 简化实现（QNN 链的库均已由 App 预加载进内存，sphal 语义降级为 dlopen 安全）。
#define _GNU_SOURCE
#include <dlfcn.h>

void* android_load_sphal_library(const char* name, int flag) {
    return dlopen(name, flag);
}

int android_unload_sphal_library(void* handle) {
    return dlclose(handle);
}

int android_is_in_vendor_process(void) {
    return 0;
}
