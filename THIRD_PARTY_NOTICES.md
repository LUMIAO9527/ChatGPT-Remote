# 来源与第三方组件

原生托盘、窗口停靠、磨砂玻璃、圆角、设计令牌、设置写入和动效的部分实现适配自同一作者的 [ChatGPTnx v1.6.0](https://github.com/LUMIAO9527/ChatGPTnx/tree/v1.6.0)。

Copyright (c) 2026 ChatGPTnx. 保留所有权利 / All rights reserved.

相关实现位于 `desktop_tray.py`、`desktop_platform.py`、`desktop_material.py`、`native_backdrop.cpp` 和 `WindowsAgent/assets/`。保留原有代码说明与署名，按本项目 LICENSE 发布。

Python 运行依赖包括 pywebview、Pillow、qrcode 及其依赖，分别遵循各自发布包内的许可。离线预览中的二维码和设备信息为示例数据；支持页的公开联系方式与打赏图片沿用作者的 ChatGPTnx 支持页。

原生宿主使用 Windows SDK 提供的 C++/WinRT v2.0.250303.1 投影，适用 [MIT License](https://github.com/microsoft/cppwinrt/blob/master/LICENSE)：

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
