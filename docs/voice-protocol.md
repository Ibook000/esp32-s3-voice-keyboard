# BLE Voice Protocol v1

同一设备提供 HID 及自定义语音服务，所有字段为 little endian。

| UUID | 用途 |
| --- | --- |
| `cba10000-7d2f-4a6b-9c11-18d5e0010001` | Service |
| `cba10001-7d2f-4a6b-9c11-18d5e0010001` | Control，encrypted write with response |
| `cba10002-7d2f-4a6b-9c11-18d5e0010001` | Audio，notify，encrypted CCCD |
| `cba10003-7d2f-4a6b-9c11-18d5e0010001` | Status，encrypted read/notify |

## 控制

单字节 `00` 停止录音并发送；`01` 开始麦克风录音；`02` 开始测试音；`04` 取消。
ACK 为 `03 session:u16 sequence:u16`，固定 5 字节。
回调仅入队，状态机和发送在 Arduino 主循环执行；I2S 采样由独立任务执行。

## 状态

固定 20 字节，Python 格式 `<BBBBHHHHIHH`：

| 偏移 | 字段 |
| --- | --- |
| 0 | version:u8 = 1 |
| 1 | type:u8 = 0x10 |
| 2 | state:u8：0 idle，1 recording，2 sending，3 complete，4 error |
| 3 | error:u8，含义见 `pc/voice_protocol.py` 的 `ERRORS` |
| 4 | session:u16 |
| 6 | frames:u16 |
| 8 | sample_rate:u16 = 16000 |
| 10 | frame_samples:u16 = 320 |
| 12 | total_samples:u32 = frames × 320 |
| 16 | max_seconds:u16，默认 10 |
| 18 | flags:u16：bit0 测试音，bit1 麦克风引脚已配置 |

状态在状态变化时更新；录音中的 frames 可以是初始快照。停止录音后的长度为最终长度。
完成状态保持可读，电脑每秒轮询补偿状态通知丢失。

## 音频分片

`version:u8=1 type:u8=0x20 session:u16 sequence:u16 offset:u16 payload:bytes`。
每帧编码块共 168 字节；sequence 从 0 开始，offset 为块内字节偏移。
通知大小不超过 `min(ATT_MTU-3, 244)`，音频净载荷再扣除 8 字节头部。

编码块：`count:u16=320 predictor:i16 index:u8 reserved:u8=0 adpcm:160bytes crc:u16`。
第一个 PCM 样本直接存为 predictor，后续 319 样本采用 IMA ADPCM，低 nibble 在前；末尾高 nibble 为填充。
CRC16-CCITT-FALSE：多项式 `0x1021`，初值 `0xFFFF`，无反射，无最终异或；覆盖 CRC 前全部 166 字节，CRC 自身按 little endian 存储。
每块的 predictor/index 独立，不依赖上一块。

整帧发送后等待对应 session/sequence 的 ACK，1 秒未确认从 offset=0 重发，最多重传 5 次。
电脑只在校验、解码完成后确认；收到已完成帧的重传会再次确认，不重复写音频。
收到 COMPLETE 时，电脑还要核对实际帧数，才保存 WAV 和调用转写。
控制和数据共用单个主机连接。发送中忽略新的 START；断线或取消会丢弃当前会话。
