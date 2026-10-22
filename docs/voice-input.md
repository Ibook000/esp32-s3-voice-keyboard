# 自定义 BLE 语音输入

流程：麦克风 → ESP32-S3 I2S 采集 → ADPCM 缓存 → BLE 传输 → 电脑 WAV → 本地转写。
默认每段最多 **10 秒**；松开按钮或到达上限后发送。录完再传，不是实时通话，也不会注册为系统麦克风。

## 1. 接线和固件配置

`include/voice_config.h` 引用 `include/board_config.h`，已按 2026-10-22 网表配置如下。
OLED 实际接 SDA/GPIO15、SCL/GPIO16；GPIO39/40 属于 J4 扩展接口。完整映射见 [实际接线与 GPIO](gpio-map.md)。

| 麦克风 | ESP32-S3 / 配置 |
| --- | --- |
| VDD | 3.3V |
| GND | GND |
| SCK / BCLK | GPIO4；`VOICE_I2S_BCLK = BoardPins::I2S_SCK` |
| WS / LRCLK | GPIO5；`VOICE_I2S_WS = BoardPins::I2S_WS` |
| SD / DOUT | GPIO6；`VOICE_I2S_DATA = BoardPins::I2S_SD` |
| L/R（若有） | 接 GND，选择左声道 |

配置面向 24 位标准 I2S 麦克风，不适用于模拟或 PDM 麦克风；具体接线以模块数据手册为准。
右声道麦克风需要 `VOICE_I2S_RIGHT_CHANNEL = true`。
默认按 L/R 接 GND 的左声道配置，请检查该脚没有悬空。任一麦克风引脚设为 `-1` 可禁用实际采音，保留测试音。
声音太大/失真时，将 `VOICE_MIC_SHIFT` 从 14 调至 16，降低增益。

REC 按钮为 **KEY4，接 GPIO7 与 GND 之间**，`VOICE_BUTTON_PIN = BoardPins::VOICE_PTT`。
内部上拉、低电平按下，按住录音、松开发送，25 ms 消抖；设为 `-1` 可禁用实体按钮。
不接按钮也可用电脑终端或串口开始/结束录音。

```powershell
pio run
pio run -t upload
pio device monitor
```

升级增加了新 GATT 服务，请从 Windows 中移除旧 `ibook Keyboard` 后重新配对，以刷新缓存。
电脑端会自动从 Windows 已配对设备或广播中取得地址，不需要从串口复制。

## 2. 安装电脑端（Windows）

在项目根目录运行。若已有 `.venv`，不需要再次创建。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r pc/requirements-asr.txt
```

仅测试接收和 WAV 时，改为安装 `pc/requirements.txt`。依赖保存在项目虚拟环境中。

## 3. 不接麦克风先测试传输

程序会自动查找 `ibook Keyboard`，并在成功验证语音服务后记住地址：

```powershell
.\.venv\Scripts\python.exe pc/voice_receiver.py --test-tone
```

板端生成 3 秒 440 Hz 测试音，经同样的编码、分片、确认、重传后保存至 `pc/recordings/*.wav`。
可以播放 WAV 检查时长和声音。测试音不会送入语音识别。

Windows 已作为键盘连接它时，会从系统已配对设备中查找，复用物理 BLE 连接；不依赖设备持续广播。只有多台同名设备时，才需要用 `--address` 明确选择一次。

## 统一工作台（推荐）

双击 `pc/start.cmd`，在网页连接键盘，下方勾选“启用语音接收”“识别为文字”，按需开启“自动输入当前窗口”，再点击“应用语音设置”。
等待状态“等待 KEY4”后，在目标输入框按住实体 KEY4 说话，松开发送。网页显示最近识别结果，可复制。
快捷键配置和语音现在共用一个进程和一条 BLE 连接，无需另启语音后台。日常说明见 [PC 工作台说明](../pc/README.md)。
网页“退出服务”停止工作台与语音；只关闭页面标签会继续运行。日志在 `pc/runtime/web-config.log`。
日常入口为 `pc/start.cmd`，旧版独立接收器管理工具已放入 `pc/tools/`，仅供调试。

## 4. 录音和本地语音识别

```powershell
.\.venv\Scripts\python.exe pc/voice_receiver.py --transcribe
```

- 电脑终端中 Enter 开始、再次 Enter 停止发送，或者使用实体语音按钮。
- 收齐后保存 WAV，转写结果保存为同名 `.txt`，也会在终端显示。
- `n` 开始测试音，Enter 结束；`x` 取消；`q` 退出。
- 串口命令：`v` 开始麦克风录音、`b` 结束发送、`n` 开始测试音、`x` 取消。需要先运行电脑接收程序。
- 一次性录制 5 秒并接收：追加 `--duration 5`。

识别采用 faster-whisper，默认多语言 `small` 模型、CPU int8、中文 `zh`。
**首次运行需联网下载模型**，缓存到项目 `.pio/asr_models` 后可离线使用，音频不上传到识别服务。
`--model tiny` 可用于较小模型试运行；`--model 本地模型目录` 可指定预先下载的模型。模型影响速度和准确率。

## 5. 可选：输入到当前窗口

```powershell
.\.venv\Scripts\python.exe pc/voice_receiver.py --transcribe --type-text
```

启动后先把焦点切到目标文本框，再用**实体语音按钮**录音。识别后通过 Windows Unicode 键盘事件输入文字，不更改剪贴板、不自动按 Enter。
此选项仅支持 Windows；目标程序的权限和控件兼容性可能影响输入。默认只显示并保存文字。

## 传输行为

- 16 kHz 单声道，电脑输出 PCM16 WAV。板端每 20 ms 压成独立 IMA ADPCM 块；10 秒缓冲约 84 KB，不要求 PSRAM。
- HID 与语音服务共存。控制写入和音频通知订阅要求加密连接。
- 根据实际 ATT MTU 分片，最小 MTU 23 也可传，MTU 越小传输越慢。
- 会话 ID、帧序号、偏移和 CRC16 检查完整性。电脑只确认完整有效的帧；丢片/丢 ACK 重传，重复帧不重复追加。
- 每帧最多重传 5 次。断线、取消、超时、未收齐时不保存残缺 WAV、不转写。
- 发送期间不能开始下一段。键盘主循环继续运行；电脑识别任务在后台串行执行。
- 退出电脑程序会取消录音；重开程序会取消旧进程遗留的未完成会话。
- 单主机短句语音方案，真实延迟和质量需要在实际硬件上验证。

## 常见问题

### 串口出现 `[Voice] Error 4`

表示电脑还没有订阅音频通知，录音尚未开始，不代表麦克风损坏。
先检查 `pc/tools/background.py status` 和 `pc/runtime/receiver.log`。
如果已烧录语音固件，但日志提示服务缺失、特征不完整或 Windows `0x8000FFFF`，可能是旧配对的 GATT 缓存：停止后台，只移除 `ibook Keyboard` 的配对，重新配对后再启动后台。
成功时电脑日志会出现 `Voice notifications subscribed`，后台 `connected` 为 `true`。此后再开始录音。

## 开发验证

```powershell
pio run
python -m unittest discover -s pc/tests -v
```

覆盖 C++ 编码/Python 解码互通、CRC 和边界、MTU 23 分片、丢片/丢 ACK、重复帧、丢完成通知及断线。
C++ 编解码互通测试需要 `g++`，缺少时跳过；其他测试只依赖 Python 标准库。
这些测试不能替代真实麦克风、开发板、电脑蓝牙和识别效果验证。

参考：[Bleak 客户端 API](https://bleak.readthedocs.io/en/stable/api/client.html)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[ESP32-S3 I2S](https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32s3/api-reference/peripherals/i2s.html)。
