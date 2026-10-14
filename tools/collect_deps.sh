#!/system/bin/sh
# 递归收集 QNN 依赖链（非 public 库）到 /data/local/tmp/qnn_app
qnn=/data/local/tmp/qnn_app
pub=/data/local/tmp/public_libs.txt
for round in 1 2 3 4 5 6; do
  changed=0
  for lib in $(ls "$qnn"/*.so 2>/dev/null | sed 's/.*\///'); do
    for dep in $(readelf -d "$qnn/$lib" 2>/dev/null | grep NEEDED | sed 's/.*\[\(.*\)\].*/\1/'); do
      if [ -f "$qnn/$dep" ]; then continue; fi
      if grep -qx "$dep" "$pub" 2>/dev/null; then continue; fi
      f=""
      [ -f /system/lib64/$dep ] && f=/system/lib64/$dep
      [ -z "$f" ] && [ -f /vendor/lib64/$dep ] && f=/vendor/lib64/$dep
      if [ -n "$f" ]; then
        cp "$f" "$qnn/$dep" && chmod 755 "$qnn/$dep" && echo "R$round $dep" && changed=1
      fi
    done
  done
  [ "$changed" = "0" ] && break
done
echo "TOTAL=$(ls "$qnn"/*.so | wc -l)"
