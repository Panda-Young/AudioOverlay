#!/system/bin/sh
echo "=== libvndksupport UND symbols ==="
readelf -s /data/local/tmp/qnn_app/libvndksupport.so | awk '$7=="UND" {print $8}' | sort -u
echo "=== libvndksupport defined GLOBAL ==="
readelf -s /data/local/tmp/qnn_app/libvndksupport.so | awk '$4=="FUNC" && $7!="UND" && $8!="" {print $8}' | sort -u
echo "=== libdl_android(stub) defined ==="
readelf -s /data/local/tmp/qnn_app/libdl_android.so | awk '$4=="FUNC" && $7!="UND" && $8!="" {print $8}' | sort -u
