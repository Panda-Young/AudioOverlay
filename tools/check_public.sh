#!/system/bin/sh
for l in libdl_android.so libvndksupport.so libutils.so libcutils.so libhidlbase.so libhardware.so libbase.so libc++.so liblog.so android.hardware.common-V2-ndk.so libz.so libc.so libm.so; do
  if grep -qx "$l" /data/local/tmp/public_libs.txt; then
    echo "$l: PUBLIC"
  else
    echo "$l: NOT_PUBLIC"
  fi
done
