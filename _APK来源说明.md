# APK 本地来源说明（重要，供任务执行者使用）

以下为测试矩阵所需第三方视频 App 的**本地 APK 来源**，优先使用，避免重复下载：

- **抖音**（已下载，APKPure 渠道，38.7.0 版）：
  `C:\Users\16411\Downloads\APK\抖音+-+记录美好生活_38.7.0_APKPure.apk`

同一目录下还有其他 APK 可参考：
- `C:\Users\16411\Downloads\APK\` （内含 steamlink-android.apk、FTP Server APK 等）

使用方式：`adb -s <serial> install -r "C:\Users\16411\Downloads\APK\抖音+-+记录美好生活_38.7.0_APKPure.apk"`

说明：用户本人确认该 APK 就是在模拟器/虚拟机上用的安装包，来源为 APKPure，可信。
若测试矩阵需要 B站、腾讯视频、YouTube 等 App 且本地无 APK，可从 apkpure.com 自行下载（用户已认可该渠道）。
