#pragma once

class U8G2;
namespace DisplayModel {
struct View;
}

namespace DisplayRenderer {
// 128x64 像素排版；只写帧缓冲，不访问 I2C、不发送按键、不修改录音状态。
// 由显示任务完成 sendBuffer()，也可在电脑上复用此绘制函数预览真实字库。
void draw(U8G2& display, const DisplayModel::View& view);
}  // namespace DisplayRenderer
