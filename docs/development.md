# 开发与代码检查

固件使用 PlatformIO、ESP32-S3 和 Arduino。`platformio.ini` 固定 Espressif32 平台版本，避免 BLE API 随依赖升级发生变化。
电脑端使用 Python 3.11 或更新版本，Windows 后台功能通过 WinRT 和 Win32 API 实现。

## 安装开发工具

在项目根目录执行。已有虚拟环境时跳过创建命令。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r pc/requirements-dev.txt
```

需要真实语音转写时，另行安装 `pc/requirements-asr.txt`。
单元测试通过模拟外部服务验证转写调用，不下载模型、不触发真实文字输入。

## 检查

```powershell
pio run
.\.venv\Scripts\python.exe -m unittest discover -s pc/tests -v
.\.venv\Scripts\ruff.exe check pc
.\.venv\Scripts\ruff.exe format pc --check --quiet
$sourceFiles = @(rg --files src include pc/tests -g '*.cpp' -g '*.h')
& .\.venv\Scripts\clang-format.exe --dry-run --Werror @sourceFiles
```

C++ 编码互通、快捷键、旋钮和 OLED 显示状态测试需要本机 `g++`；缺少时跳过对应测试。其余测试仅依赖 Python 标准库。
OLED 测试验证界面状态转换、中文提示、计时、进度边界和快捷键标签。
快捷键测试使用模拟 GPIO 和 BLE，直接编译执行 `src/shortcut_keys.cpp`，覆盖键值绑定、消抖、松键、
同时按键、开机按住、断线重连、发送失败重试、手动释放及计时回绕，不发送真实电脑按键。
旋钮测试覆盖双卡点、反弹、非法跳变、快速积压上限、按压单次确认、开机/重连按住、快捷键合并、发送失败及计时回绕。
自动检查不会烧录开发板，也不会清除配对。

## USB 启动与蓝牙诊断

默认 `esp32-s3-devkitc-1` 环境将日志输出到 J3/UART0。使用板上 J1/USB-C 诊断时，选择
`esp32-s3-usb-debug` 环境，启用硬件 USB Serial/JTAG CDC 和 BLE 库警告、错误日志，避免大量调试输出掩盖硬件状态。
两个环境使用相同的键盘、快捷键和语音逻辑；无参数的 `pio run` 仍只构建默认环境。

```powershell
pio run -e esp32-s3-usb-debug
pio run -e esp32-s3-usb-debug -t upload --upload-port COM24
pio device monitor -e esp32-s3-usb-debug --port COM24
```

COM24 是本次连接的端口，换电脑或插口后以实际枚举结果为准。
烧录前关闭串口监视器和串口助手，避免端口被占用。程序启动不等待串口终端；
输入 `s` 查看状态，输入 `d` 切换四个按钮的变化日志。

诊断时同时查看实际扫描和连接状态，不能只凭打印了广播启动信息就认定无线广播已成功。
在串口输入 `s`，输出 `Connected: yes, ready: yes` 表示链路已建立、认证完成且本地 HID 通知已启用；不代表每份报告都已被 Windows 接收。
同一命令还会输出 OLED 地址及 SDA/SCL 引脚；地址为 `0x00` 表示目前没有已初始化的屏幕。
板子 USB 标识对应的基础 MAC 和蓝牙 MAC 可能不同，应以 `[BLE] Address:` 输出为准。

若完全搜不到设备，先松开 BOOT 后复位，检查启动日志是否反复重启或停在某个初始化阶段；
若 USB 传输超时后端口无法打开，可关闭串口工具并重新插拔 USB。

## 格式化

```powershell
.\.venv\Scripts\ruff.exe check pc --fix
.\.venv\Scripts\ruff.exe format pc
$sourceFiles = @(rg --files src include pc/tests -g '*.cpp' -g '*.h')
& .\.venv\Scripts\clang-format.exe -i @sourceFiles
```

Python 规则在 `pyproject.toml`，C++ 规则在 `.clang-format`，基础缩进与换行规则在 `.editorconfig`。

## 修改位置

- 全板引脚定义：`include/board_config.h`，映射来源见 `docs/gpio-map.md` 和 `hardware/Netlist_Schematic1_2026-10-22.tel`。
- 语音引脚引用、采音增益、单次录音时长：`include/voice_config.h`。
- 实体快捷键组合及消抖时间：`include/shortcut_config.h`；扫描和报告逻辑：`src/shortcut_keys.cpp`。
- OLED 控制器、地址、刷新和亮度：`include/display_config.h`；界面：`src/display_renderer.cpp`；显示任务：`src/status_display.cpp`。
- OLED 只读状态：`src/display_model.cpp`；按键和录音状态机在主循环中生成快照，经单元素队列送给屏幕任务。
- 旋钮方向与时序：`include/encoder_config.h`；双相解码：`include/rotary_decoder.h`；采集和控制：`src/rotary_encoder.cpp`。
- 设备名称、串口速率、主循环间隔：`src/main.cpp`。
- 固件协议 UUID、采样率、帧长：`include/voice_protocol.h`。
- 电脑端协议：`pc/voice_protocol.py`；修改时同步固件和 `docs/voice-protocol.md`。
- 语音识别和 WAV 格式：`pc/voice_audio.py`。
- Windows 文字输入：`pc/windows_input.py`。

## 本地产物

| 目录 | 内容 |
| --- | --- |
| `.pio/build/` | 编译产物 |
| `.pio/asr_models/` | 识别模型缓存 |
| `.venv/` | 项目 Python 环境 |
| `pc/runtime/` | 后台日志、状态及已验证设备地址 |
| `pc/recordings/` | 用户录音与转写文本 |

这些目录已加入 `.gitignore`。整理时保留录音、模型、Python 环境和用户配置。编译对象文件、缓存和重复依赖可重建；最新 firmware.bin、firmware.elf、bootloader.bin 和 partitions.bin 保留供烧录和调试。显示库只使用默认 `.pio/libdeps/`，不再维护重复的 `oled-libdeps`。

## Windows 已连接但键盘不就绪

旧状态 `Connected: yes, ready: no` 可能是 Arduino-ESP32 2.x 的本地 HID CCCD 状态在复位后丢失，
Windows 重连时未重新写订阅。本固件在认证成功后启用本地 HID 通知，在新连接开始、断开或认证失败时清除；
主机后续显式写入 CCCD 仍然生效。该兼容处理只作用于键盘，不会替 PC 语音程序订阅音频。
实现参考 [ESP32-BLE-Keyboard 0.3.2-beta](https://github.com/T-vK/ESP32-BLE-Keyboard/blob/0.3.2-beta/BleKeyboard.cpp#L477)。
`ready` 表示本地具备提交报告的条件，不能代替实际记事本输入验证。

## Web 配置开发

协议、文件职责和启动方法见 [Web 配置说明](web-config.md)。
`rtk proxy node --test pc/web/tests/model.test.js` 检查网页草稿、按键录入映射和名称边界。
Python 回归包含本地 HTTP 接口、来源校验、Python 客户端与真实 C++ 配置事务互通、错误回滚和重启恢复模拟。
这些测试使用模拟 BLE，不会改写已配对键盘的实际配置。

## 整理后的入口

- `pc/start.cmd`：启动统一工作台。
- `pc/status.cmd`：查看状态。
- `pc/stop.cmd`：停止服务。
- `pc/tools/background.py`：旧版独立语音接收器诊断工具，不作为日常入口。

清理编译中间文件后，下一次 PlatformIO 构建会重新生成对象文件，耗时会比增量构建长。固件源码和配置没有因清理而改变。
