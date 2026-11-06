# ChatGPT Remote

<picture><source media="(prefers-color-scheme: dark)" srcset="WindowsAgent/assets/rm-mark-light.svg"><img src="WindowsAgent/assets/rm-mark-dark.svg" width="64" alt="Rm"></picture>

通过手机浏览器连接电脑上的 ChatGPT，继续查看对话、发送消息和操作窗口。Windows 端常驻 **Rm** 托盘。

公开下载版本以 [GitHub Releases](https://github.com/LUMIAO9527/ChatGPT-Remote/releases/latest) 为准。

**1.0.0** 提供手机审批、紧凑弹层和断线恢复。双击 `ChatGPT-Remote-Preview.html` 可查看交互预览。[手机界面与连接说明](docs/MOBILE-REDESIGN.md)

- 更新在“设置 → 版本更新”内完成下载、校验和重启安装。
- 手机发送键在有草稿时用于发送或排队，在空输入且有原生停止动作时显示停止。队列继续、引导、删除和编辑留在输入框上方。
- 设置、模型、项目、草稿、帮助、配对和电脑弹窗通过底部弹层操作。电脑画面从对话中展开，可手动放大。
- 电脑弹窗显示请求说明和可用的原生选择。审批点选绑定当前窗口与当前请求，完成后回读。
- 切网和回前台会重新连接。控制通道不可用时新操作走 HTTP，文字流中断时定期读取对话快照。
- 输入框下方直接选择电脑原生菜单中的模型，切换时保留手机草稿。
- 画面解码与界面状态更新分开，减少重复布局；远程模式最高 20 FPS，实际帧率由电脑、网络和手机共同决定。
- 控件使用底色、细边框与轻量反馈，减少悬停阴影和手机弹层的背景模糊。
- 紧凑主面板直接显示二维码、配对码、连接地址、窗口与设备。
- 点击托盘图标展开，再次点击收起；每次展开回到首页，展开时置顶。
- 312×458 面板、16px 外壳圆角、ChatGPTnx 的深浅配色与原生磨砂玻璃。
- Rm 在深色界面用白底黑字，在浅色界面用黑底白字。面板跟随应用配色，托盘跟随 Windows 任务栏配色。
- 首次配对使用主电脑连接页；已配对手机直接进入当前对话，设置和项目通过手机返回键回到对话，保留正在编辑的消息。
- 手机优先显示对话文字，按需切换画面；支持项目和对话切换、回复中排队、手机文件上传。
- 设置提供开机自启、外观、版本更新和联系与支持。

## 程序与源码

- `ChatGPTRemote.exe` 是 Windows x64 独立程序。
- `ChatGPT-Remote-v1.0.0-source.zip` 提供同版本源码、构建入口和开发说明。
- `ChatGPT-Remote-Preview.html` 提供离线预览，可切换主题、尺寸和审批场景。
- `SHA256SUMS.txt` 提供发布文件的 SHA-256。

## 第一次使用

1. 打开独立程序，选择程序文件夹。程序与配对数据保存在这里，安装后会登记到 Windows 的“已安装的应用”。
2. 电脑安装并登录 Tailscale，打开 ChatGPT 并登录。当前独立版的远程入口依赖电脑上的 Tailscale。
3. 在电脑另行启用 Funnel：`tailscale funnel --bg --https=8443 http://127.0.0.1:8765`。首次可能需要在 Tailscale 页面确认启用条件；Rm 目前不会自动完成这一步。
4. 打开 Remote 首页，启动服务。首次启用会配置当前用户的后台任务。
5. 手机扫描 Rm 提供的完整连接二维码，再输入配对码并选择窗口。Funnel 模式下手机只需浏览器，不必安装 Tailscale，也不必登录电脑的 Tailscale 账号。

需要 64 位 Windows 10/11 和 Microsoft Edge WebView2。下载 EXE 不会自动建立公网入口；源码中的 `WindowsAgent/setup_funnel_windows.bat` 可完成上述 Funnel 配置。请使用自己的电脑与账号，不使用作者的连接网址。

当前公网路径是“手机浏览器 → Tailscale Funnel 中继 → 电脑上的 Tailscale → Rm → ChatGPT 窗口”。网页、操作接口和画面流由电脑提供，GitHub 只分发程序与更新。服务显示运行中，不代表公网入口已就绪；电脑经 Tailscale 内网能打开，同样不能替代手机网络的实际检查。

[首次使用、更新、卸载和数据说明](docs/LIFECYCLE.md)

## 从源码运行

需要 64 位 Python 3.10 或更新版本、PowerShell 7。先执行 `WindowsAgent/setup_desktop.ps1 -InstallDependencies`，再打开生成的快捷方式。源码运行数据在 `WindowsAgent/data`，依赖和缓存放在 `_wip`。源码更新使用源码包或 Git；内置程序替换只适用于独立 EXE。

## 开发与检查

```powershell
./Verify.ps1
python -B tools/build_review.py
```

界面检查需要 Node.js，运行程序不需要。重建玻璃宿主使用 `python -B build_desktop.py`，需要 MSVC C++ Build Tools 和 Windows SDK；发布包已包含宿主。

[界面规范](docs/DESIGN.md) · [接口说明](docs/API.md)

## 数据

独立程序的配对、窗口选择和访问密钥保存在所选程序文件夹的 `_data/`；源码模式保存在 `WindowsAgent/data/`。运行缓存与更新恢复材料位于 `_wip/`，独立程序的手机上传文件属于 `_data/`。更新保留配对和设置。对话正文不写入程序日志；发布包不包含本机配对、密钥或运行数据。

本机 Codex 对话从原有 `.codex/state_*.sqlite`、`thread_history_*.sqlite` 和 `queue_*.sqlite` 只读取得，保留原生角色、消息编号和轮次状态；项目重复打开复用网页内缓存。公开思考摘要和工作记录折叠，正文按 Markdown 段落、列表、标题和代码显示。输入、发送及附件附加通过桌面原生控件完成；其它可访问界面继续使用辅助功能读取。

## 联系与支持

[问题反馈](https://github.com/LUMIAO9527/ChatGPT-Remote/issues) · QQ **210037309**。程序内的“联系与支持”提供复制联系方式和作者打赏码。

[许可](LICENSE) · [来源与第三方组件](THIRD_PARTY_NOTICES.md)
