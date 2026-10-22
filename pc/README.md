# PC 键盘工作台

日常只用一个入口：**双击 `pc/start.cmd`**。浏览器打开 <http://127.0.0.1:8787>，在同一页面设置快捷键和语音输入。
`status.cmd` 查看工作台状态，`stop.cmd` 停止服务；其余 Python 文件由程序自动调用。

## 首次准备

项目根目录执行（已有 `.venv` 时跳过创建）：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r pc/requirements-asr.txt
```

只改键或保存录音可安装 `pc/requirements.txt`。语音识别需要额外的 `requirements-asr.txt`。
Windows 蓝牙设置中先配对 **ibook Keyboard**；首次升级带 Web 配置的固件后删除旧配对并重新添加一次。
工作台只会对支持相应功能的固件开放保存。旋钮音量与深睡设置需要配套新版固件；已使用带音量的固件时，仅升级深睡通常不必重新配对。

## 日常使用

1. 双击 `start.cmd`，点击网页右上角“连接键盘”。
2. 快捷键：选择 KEY1～KEY3，设置名称和组合键，再点击“保存到键盘”。
3. 语音：点击网页 KEY4，在右侧勾选“启用语音接收”“识别为文字”，按需勾选“自动输入当前窗口”，点击“应用语音设置”。
4. 等待状态显示“等待 KEY4”。在目标输入框中，按住实体 KEY4 说话，松开发送。每段最多 10 秒。
5. 网页右侧显示最近 8 次识别结果，可复制。自动输入不按 Enter，文字会输入识别完成时的焦点窗口。
6. 结束时点击网页“退出服务”，会同时停止语音和工作台。只关闭浏览器标签，后台仍运行。

实体排列从左到右是 **KEY4（录音）、KEY1、KEY2、KEY3、旋钮**。点击网页旋钮可切换“页面滚动 / Enter”或“电脑音量 / 静音”。需要本次支持媒体控制的固件，并在 Windows 删除旧配对后重新添加。
网页没有录音热键，不使用电脑麦克风。录音由实体键控制。

语音设置保存在电脑 `runtime/voice-settings.json`；快捷键保存在键盘 Flash 中。
再次打开工作台会恢复语音开关、模型和上次选择的键盘，并尝试连接；断线会重试同一地址。
未启用语音时只运行按键配置。停用语音不会断开配置连接；“断开键盘连接”会暂停语音并停止重连，点击连接恢复。
语音识别在本机 CPU 运行。默认 tiny，也可选 base 或 small；首次加载可能联网下载模型。

## 文件与入口

| 文件 | 作用 |
| --- | --- |
| `start.cmd` | 唯一日常启动入口 |
| `web_config.py` | 本地网页、HTTP API、后台事件循环 |
| `keyboard_app.py` | 共用 BLE 连接、语音开关、模型任务、识别结果、断线重连 |
| `keymap_client.py` / `keymap_protocol.py` | 快捷键传输、保存确认和回读校验 |
| `voice_receiver.py` | 音频订阅、分片接收、重传确认、完整录音保存 |
| `voice_protocol.py` | 字节协议、CRC 和 ADPCM 解码 |
| `voice_audio.py` | 保存 WAV 和本地语音识别 |
| `speech_process.py` | 管理语音子进程及异常退出 |
| `windows_input.py` | Unicode 文字输入当前窗口 |
| `voice_discovery.py` | 蓝牙设备发现和缓存 |
| `voice_background.py` | 语音单实例互斥锁及独立接收器诊断基础设施 |
| `tools/background.py` | 旧版独立接收器管理工具，日常不需要运行 |
| `web/` | 界面、快捷键录入和语音结果展示 |
| `tests/` | 自动化测试，日常不需要运行 |

文件分工保留便于维护；日常仍只有一个启动入口，快捷键配置和语音共用一条蓝牙连接。语音引擎由工作台管理为独立子进程，原生库异常退出时不会关闭网页和蓝牙连接；停用语音会结束子进程。

## 状态、录音和退出

- `recordings/`：WAV 与同名 TXT。
- `runtime/asr-worker.log`：模型加载、识别及原生崩溃诊断。
- `runtime/web-config.log`：工作台日志；`runtime/voice-settings.json`：语音选项。
- `runtime/workbench.json`：上次连接地址；`.pio/asr_models/`：本地模型缓存。
- 识别历史仅保留本次运行最近 8 条；完整文字仍保存到 TXT。
- `status.cmd` 查看工作台状态；`stop.cmd` 停止整个工作台。

命令行管理：

```powershell
.venv/Scripts/python.exe pc/web_config.py --open
.venv/Scripts/python.exe pc/web_config.py --status
.venv/Scripts/python.exe pc/web_config.py --stop
```

端口冲突可添加 `--port 8788`；对应页面改为 <http://127.0.0.1:8788>。
如果旧版独立语音后台还在运行，先用 `.venv/Scripts/python.exe pc/tools/background.py stop` 停止。工作台与旧版接收器共用互斥锁，避免同时订阅音频。

## 调试

前台诊断入口保留，但不要与工作台语音同时运行：

```powershell
.venv/Scripts/python.exe pc/voice_receiver.py --test-tone
.venv/Scripts/python.exe pc/voice_receiver.py --transcribe --model tiny
```

前台终端 Enter/n/x/q 操作仅用于诊断，日常无需终端。
更多协议与开发说明见 [Web 配置说明](../docs/web-config.md)、[开发说明](../docs/development.md)。

## 自动深度睡眠

页面顶部“深度睡眠”可设置闲置时间，默认 10 分钟，也可关闭。只在深睡前关闭 OLED，没有独立熄屏计时。按实体按钮唤醒，首次按压只唤醒，松开后等蓝牙重连。需烧录支持深睡的新固件。详见 [深睡说明](../docs/deep-sleep.md)。

## 电量显示现状

目前没有真实电量检测，蓝牙固定上报的 100% 是占位值。ADC 分压接线只是已讨论的方案，尚未接入软件；网页和 OLED 暂不显示真实电量。
