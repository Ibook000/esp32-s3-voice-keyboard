# 实际接线与 GPIO

接线来源为用户提供的 [2026-10-22 网表](../hardware/Netlist_Schematic1_2026-10-22.tel)，已保留原文件副本。
模块为 U1 / ESP32-S3-WROOM-1。焊盘到 GPIO 的换算依据为乐鑫官方
[模块数据手册 v1.8，第 3.2 节、表 3-1](https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf)。

注意：`U1.19` 是模块第 19 焊盘，对应 **GPIO11**；固件必须填写 GPIO 编号，不能直接填写焊盘号。
所有 GPIO 定义集中在 [board_config.h](../include/board_config.h)。

## 麦克风和录音按钮

| 功能 / 网络 | 网表连接 | U1 焊盘 | GPIO | 固件配置 |
| --- | --- | --- | --- | --- |
| 麦克风位时钟 / I2S_SCK | U2.6 | 4 | 4 | `BoardPins::I2S_SCK` → `VOICE_I2S_BCLK` |
| 麦克风左右声道时钟 / I2S_WS | U2.5 | 5 | 5 | `BoardPins::I2S_WS` → `VOICE_I2S_WS` |
| 麦克风音频数据 / I2S_SD | U2.3 | 6 | 6 | `BoardPins::I2S_SD` → `VOICE_I2S_DATA` |
| KEY4 录音键 / VOICE_PTT | KEY4.1，KEY4.2 接 GND | 7 | 7 | `BoardPins::VOICE_PTT` → `VOICE_BUTTON_PIN` |

U2.2 接 3V3，U2.1 和 U2.4 接 GND；沿用 INMP441 左声道采集配置。
KEY4 启用内部上拉，低电平按下，25 ms 消抖；按住录音、松开发送，最长 10 秒。
电脑接收器需已连接并订阅音频通知后才能开始录音。

原固件的 BCLK/WS/SD 为 GPIO15/16/7，录音键为 GPIO47，现已替换。

## 其他外设

| 功能 / 网络 | 网表连接 | U1 焊盘 | GPIO | 当前固件状态 |
| --- | --- | --- | --- | --- |
| KEY1 | KEY1.1；另一端接 GND | 12 | 8 | Ctrl+C，复制 |
| KEY2 | KEY2.1；另一端接 GND | 17 | 9 | Ctrl+V，粘贴 |
| KEY3 | KEY3.1；另一端接 GND | 18 | 10 | Ctrl+Z，撤销 |
| ENC_A | ENC1.A、R8.2 | 19 | 11 | 双相 CHANGE 中断解码，默认顺时针向下滚动 |
| ENC_B | ENC1.B、R9.2 | 20 | 12 | 同上，逆时针向上滚动 |
| ENC_SW | ENC1.D、R10.2 | 21 | 13 | 按压消抖后发送一次 Enter 确认 |
| OLED SDA | OLED1.4 | 8 | 15 | I2C 状态屏，默认 SSD1306 128×64 |
| OLED SCL | OLED1.3 | 9 | 16 | 同上，显示按键、蓝牙及录音状态 |
| UART0_TXD | J3.2 | 37 | 43 | `Serial` 输出，115200 波特率 |
| UART0_RXD | J3.3 | 36 | 44 | `Serial` 调试命令输入 |
| USB_DN_MCU | 经 R6 接 J1.A7/B7 | 13 | 19 | 原生 USB 固定引脚；USB 诊断环境启用 CDC 日志 |
| USB_DP_MCU | 经 R5 接 J1.A6/B6 | 14 | 20 | 同上 |
| BOOT | SW5.1、R4.2 | 27 | 0 | 下载模式按钮，由启动电路处理 |

旋钮 A/B/SW 各由 R8/R9/R10 上拉到 3V3，公共端接 GND。
KEY1～KEY3 在固件中启用内部上拉，使用 25 ms 消抖，按下发送组合键、松开释放。
快捷键映射见 `include/shortcut_config.h`；功能由 BLE HID 直接完成，不依赖 PC Python 后台。
SW6 控制 `RESET_EN`，连接 U1.3 / EN，不属于可配置 GPIO。
此网表未出现扬声器模块，不能继续沿用旧说明里的扬声器引脚占用。

## J4 扩展接口

`BoardPins::EXPANSION_PINS` 按 J4 的 3～9 脚顺序记录 GPIO。

| J4 引脚 | 网络 | U1 焊盘 |
| --- | --- | --- |
| 1、2 | GND | 1、40、41 所在地网 |
| 3 | GPIO38 | 31 |
| 4 | GPIO39 | 32 |
| 5 | GPIO40 | 33 |
| 6 | GPIO41 | 34 |
| 7 | GPIO42 | 35 |
| 8 | GPIO2 | 38 |
| 9 | GPIO1 | 39 |
| 10 | 3V3 | 2 所在电源网 |

## 使用与验证

```powershell
pio run
pio run -t upload
```

当前 UART0 明确使用 RX/GPIO44、TX/GPIO43。观察日志时，将 3.3V 逻辑电平的 USB 转串口适配器
TX 接 J3.3、RX 接 J3.2、GND 接 J3.1。J1 是直连芯片 GPIO19/20 的原生 USB；默认环境不在这里输出日志，
`esp32-s3-usb-debug` 环境则将 `Serial` 切换到 J1，详见 [USB 诊断说明](development.md#usb-启动与蓝牙诊断)。

现已启用 KEY1～KEY3 快捷键、KEY4 录音按钮、麦克风、OLED 和 UART0；EC11 旋钮也已接入；扩展 GPIO 的常量用于后续开发。
旋钮方向、接线、重新配对和测试见 [EC11 使用说明](encoder.md)。
屏幕型号和地址配置见 [OLED 使用说明](oled-display.md)。
网表与 GPIO 核对、编译可以确认软件配置，但实际焊接、麦克风采集和按钮动作仍需要上板验证。
