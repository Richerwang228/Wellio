# Wellio iPhone 模拟器演示

这份工程使用 Capacitor 8.5.2 包装现有 TanStack Start 页面。前端和后端继续在 Mac 上运行；iPhone 模拟器中的 Wellio 使用 WKWebView 加载指定地址。

## 打开演示

本机已安装 Xcode 和 iOS 运行环境后，可以双击同目录的 **打开 iPhone 演示.command**。它执行下面的启动流程。

安装完整 Xcode 26+，完成首次启动的组件安装，在 Xcode Settings → Components 安装一套 iOS Simulator 运行环境。无需安装 Android 模拟器或 CocoaPods；原生依赖通过 Swift Package Manager 管理。

在 wellio-app 目录运行：

```sh
npm run ios:preview
```

默认打开 `http://127.0.0.1:3102/today` 的独立 UI 预览。没有运行中的预览时，启动现有开发演示适配器；它使用演示数据，不是实连 AI 验收。若脚本启动了预览，请保留终端；Ctrl+C 会停止它自己启动的预览，不停止原有服务。

真实前后端联调完成后，指定对应地址：

```sh
npm run ios:preview -- --url http://127.0.0.1:3100/today
```

显式指定地址时，脚本要求服务已经启动，不自动切到演示数据。

指定已有 iPhone 设备名称或 UDID：

```sh
npm run ios:preview -- --device 'iPhone 17'
```

脚本会同步配置、选择已有的 iPhone 模拟器、编译、安装和打开 Wellio。设备名称以本机 Xcode 的实际列表为准。

## 工程与资源

- `ios/App/App.xcodeproj`：原生工程。
- `capacitor.config.ts`：应用 ID `app.wellio.demo`，名称 Wellio，奶油色背景、移动内容模式、键盘原生缩放。
- `native-shell/`：未配置演示地址时的本地启动说明。它不是整个 TanStack 服务端的静态导出。
- `ios/App/App/Assets.xcassets/`：使用已确定的牛油果 logo 导出的图标、启动画面资源。
- `.native-build/`：本机编译产物，不上传 Git。
- `ios/App/App/capacitor.config.json`：按环境生成，不上传 Git；演示地址不固化到配置源码。

`npm run ios:sync` 只同步，不编译；`npm run ios:open` 打开 Xcode 工程。

如果 Xcode 不在 `/Applications/Xcode.app`，设置 `DEVELOPER_DIR` 指向它的 `Contents/Developer`。脚本不会替换全局命令行工具路径。

## 演示边界

本次为本机模拟器演示，使用开发地址与允许本地 HTTP 的配置。App Store 发布前需要单独配置正式资源/API 地址和网络策略。当前没有做 Android 工程或商店签名发布。模拟器无法拍摄真实相机画面，可用照片库图片演示食物/菜单上传。

验收应确认：图标启动、Today → Workout、直接再次启动、对话输入时键盘不遮挡发送、照片库选择、训练动画以及应用建议后的页面状态。原生验收记录以 `../docs/delivery/ios-demo-20260912/STATUS.md` 为准。

参考：[Capacitor iOS](https://capacitorjs.com/docs/ios)、[开发服务加载](https://capacitorjs.com/docs/guides/live-reload)、[键盘配置](https://capacitorjs.com/docs/apis/keyboard)。
