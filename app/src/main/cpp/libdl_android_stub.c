// libdl_android stub: 导出 android_dl* 符号，转发到系统 libdl。
// 目的：libvndksupport.so 的 DT_NEEDED 引用 libdl_android.so（其真实 NEEDED ld-android.so
// 在 App namespace 不可见），此 stub 以同名 SONAME 提供这些符号，绕开 linker 依赖。
#define _GNU_SOURCE
#include <dlfcn.h>
#include <link.h>

void* android_dlopen_ext(const char* filename, int flags, const void* extinfo) {
    (void)extinfo;
    return dlopen(filename, flags);
}

void* android_dlsym(void* handle, const char* symbol) {
    return dlsym(handle, symbol);
}

void* android_dlvsym(void* handle, const char* symbol, const char* version) {
    (void)version;
    return dlsym(handle, symbol);
}

int android_dladdr(const void* addr, Dl_info* info) {
    return dladdr(addr, info);
}

int android_dlclose(void* handle) {
    return dlclose(handle);
}

int android_dl_iterate_phdr(int (*callback)(struct dl_phdr_info*, size_t, void*), void* data) {
    return dl_iterate_phdr(callback, data);
}

// libvndksupport 引用（linker 导出符号，App namespace 不可见）。
// 语义：返回指定名称的 exported namespace；App 进程没有 SPHAL namespace，
// 返回 NULL 让调用方（android_load_sphal_library）回退到普通 dlopen。
typedef void* android_namespace_t;
android_namespace_t* android_get_exported_namespace(const char* name) {
    (void)name;
    return NULL;
}
