# BLE Voice Protocol v2

同一设备提供 HID 及自定义语音服务，所有字段为 little endian。
版本 2 的核心变化：录音时就逐帧发送（边录边发），ACK 改为「下一期望帧号」的累积确认。
电脑端仍兼容版本 1 的旧固件；版本 1 是录完整段再发送、ACK 回显帧号。

| UUID | 用途 |
| --- | --- |
| `cba10000-7d2f-4a6b-9c11-18d5e0010001` | Service |
| `cba10001-7d2f-4a6b-9c11-18d5e0010001` | Control，encrypted write with response |
| `cba10002-7d2f-4a6b-9c11-18d5e0010001` | Audio，notify，encrypted CCCD |
| `cba10003-7d2f-4a6b-9c11-18d5e0010001` | Status，encrypted read/notify |

## 控制

单字节 `00` 停止录音并冲尾；`01` 开始麦克风录音；`02` 开始测试音；`04` 取消。
ACK 固定 5 字节：`03 session:u16 next_sequence:u16`，含义是「这一帧及之前的都已收到，下一条要发 next_sequence」。
回调仅入队，状态机、发送和重传在 Arduino 主循环执行；I2S 采样由独立任务执行。

## 状态

固定 20 字节，Python 格式 `<BBBBHHHHIHH`：

| 偏移 | 字段 |
| --- | --- |
| 0 | version:u8 = 2 |
| 1 | type:u8 = 0x10 |
| 2 | state:u8：0 idle，1 recording，2 sending，3 complete，4 error |
| 3 | error:u8，含义见 `pc/voice_protocol.py` 的 `ERRORS` |
| 4 | session:u16 |
| 6 | frames:u16，录音中持续增长，松键后冻结 |
| 8 | sample_rate:u16 = 16000 |
| 10 | frame_samples:u16 = 320 |
| 12 | total_samples:u32 = frames × 320 |
| 16 | max_seconds:u16，默认 10 |
| 18 | flags:u16：bit0 测试音，bit1 麦克风引脚已配置 |

版本 2 的状态语义：
- `recording`：采音和发送同时进行，`frames` 每 250 ms 刷新一次，电脑不能拿它当最终长度。
- `sending`：按键已松开，停止采音，把剩余帧发完并等最后几个 ACK。
- `complete`：所有帧都被电脑累积确认过。
- 状态通知可能丢失，电脑每秒仍会补读一次状态。

## 音频分片

`version:u8=2 type:u8=0x20 session:u16 sequence:u16 offset:u16 payload:bytes`。
每帧编码块共 168 字节；sequence 从 0 开始，offset 为块内字节偏移。
通知大小不超过 `min(ATT_MTU-3, 244)`，音频净载荷再扣除 8 字节头部。
MTU 247 时一帧正好一个通知（176 字节），即每秒约 50 个通知。

编码块：`count:u16=320 predictor:i16 index:u8 reserved:u8=0 adpcm:160bytes crc:u16`。
第一个 PCM 样本直接存为 predictor，后续 319 样本采用 IMA ADPCM，低 nibble 在前；末尾高 nibble 为填充。
CRC16-CCITT-FALSE：多项式 `0x1021`，初值 `0xFFFF`，无反射，无最终异或；覆盖 CRC 前全部 166 字节，CRC 自身按 little endian 存储。
每块的 predictor/index 独立，不依赖上一块，所以丢帧重传不会产生累积误差。

## 流控与重传

- 板端允许最多 8 帧未被确认；不足时连续发送，窗口满就等 ACK。
- 电脑每解码成功一帧，累积帧号前移；攒 4 帧或 120 ms 没有新帧才回一次 ACK，避免每秒 50 次写往返。
- 窗口满或松键发完尾帧后，600 ms 未确认新的帧：板端从已确认的位置重发，最多 5 次，随后报 ACK 超时。电脑收到重发的旧帧会补发累积 ACK，音频只解码一次。
- 电脑只在校验、解码完成后确认；收到已完成帧的重传只重复确认，不重复写音频。
- 中间缺片时电脑等下一轮 offset=0 重传，不拼接错位音频。
- 收到 COMPLETE 时，电脑核对实际帧数与上报一致才保存 WAV 并定稿识别。
- 控制和数据共用单个主机连接。发送中忽略新的 START；断线或取消会丢弃当前会话。
- 电脑侧边收边识别：每解出一帧就推给流式识别器，句末停顿定稿一句；松键后补完尾巴。
