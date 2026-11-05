# ChatGPT Remote

<picture><source media="(prefers-color-scheme: dark)" srcset="WindowsAgent/assets/rm-mark-dark.svg"><img src="WindowsAgent/assets/rm-mark-light.svg" width="64" alt="Rm"></picture>

通过手机浏览器连接电脑上的 ChatGPT，继续查看对话、发送消息和操作窗口。Windows 端常驻 **Rm** 托盘。

公开下载版本以 [GitHub Releases](https://github.com/LUMIAO9527/ChatGPT-Remote/releases/latest) 为准。

本地维护版本：**v0.18.1**。手机队列提供原生引导、编辑、删除、继续及停止入口；操作前核对当前对话和队列项，操作后回读结果。公开过程说明直接显示，内部审查任务不进入对话目录，同一对话重连保留阅读位置。独立程序保留首次选择位置、版本更新、卸载和旧版配对导入。
- 紧凑主面板直接显示二维码、配对码、连接地址、窗口与设备。
- 点击托盘图标展开，再次点击收起；每次展开回到首页，展开时置顶。
- 312×458 面板、16px 外壳圆角、ChatGPTnx 的深浅配色与原生磨砂玻璃。
- Rm 标识提供白底黑字与黑底白字两套；面板随应用配色，托盘随 Windows 任务栏配色切换。
- 首次配对使用主电脑连接页；已配对手机直接进入当前对话，设置和项目通过手机返回键回到对话，保留正在编辑的消息。
- 手机优先显示对话文字，按需切换画面；支持项目和对话切换、回复中排队、手机文件上传。
- 设置提供开机自启、外观、版本更新和联系与支持。

## 程序与源码

- `ChatGPTRemote.exe`：Windows 独立程序，日常使用无需安装 Python。
- `ChatGPT-Remote-v0.18.1-source.zip`：源码、构建脚本和开发说明。
- `ChatGPT-Remote-Preview.html`：使用示例数据的离线界面预览。
- `SHA256SUMS.txt`：发布文件的校验和。

本地构建与 GitHub 已发布版本分别记录；[Releases](https://github.com/LUMIAO9527/ChatGPT-Remote/releases/latest) 当前公开版本见页面。

## 第一次使用

1. 打开独立程序，选择程序文件夹。程序与配对数据保存在这里，安装后会登记到 Windows 的“已安装的应用”。
2. 电脑和手机登录同一 Tailscale 网络；电脑打开 ChatGPT 并登录。
3. 打开 Remote 首页，启动服务。首次启用会配置当前用户的后台任务。
4. 手机扫描二维码并输入配对码，选择要控制的 ChatGPT 窗口。

需要 64 位 Windows 10/11 和 Microsoft Edge WebView2。外网连接仍需配置 Tailscale HTTPS 入口，步骤见 `WindowsAgent/setup_funnel_windows.bat`。

[首次使用、更新、卸载和数据说明](docs/LIFECYCLE.md)

## 从源码运行

需要 64 位 Python 3.10 或更新版本、PowerShell 7。先执行 `WindowsAgent/setup_desktop.ps1 -InstallDependencies`，再打开生成的快捷方式。源码运行数据在 `WindowsAgent/data`，依赖和缓存放在 `_wip`。源码更新使用源码包或 Git；内置程序替换只适用于独立 EXE。

## 开发与检查

```powershell
cd WindowsAgent
python -B -m unittest discover -s tests -v
node --test tests/test_web.mjs tests/test_desktop_web.mjs
python -B build_desktop.py --preview
```

界面检查需要 Node.js，运行程序不需要。重建玻璃宿主使用 `python -B build_desktop.py`，需要 MSVC C++ Build Tools 和 Windows SDK；发布包已包含宿主。

[界面规范](docs/DESIGN.md) · [接口说明](docs/API.md)

## 数据

独立程序的配对、窗口选择和访问密钥保存在所选程序文件夹的 `_data/`；源码模式保存在 `WindowsAgent/data/`。运行缓存与更新恢复材料位于 `_wip/`，独立程序的手机上传文件属于 `_data/`。更新保留配对和设置。对话正文不写入程序日志；发布包不包含本机配对、密钥或运行数据。

本机 Codex 对话从原有 `.codex/state_*.sqlite`、`thread_history_*.sqlite` 和 `queue_*.sqlite` 只读取得，保留原生角色、消息编号和轮次状态；项目重复打开复用网页内缓存。公开思考摘要和工作记录折叠，正文按 Markdown 段落、列表、标题和代码显示。输入、发送及附件附加通过桌面原生控件完成；其它可访问界面继续使用辅助功能读取。

## 联系与支持

[问题反馈](https://github.com/LUMIAO9527/ChatGPT-Remote/issues) · QQ **210037309**。程序内的“联系与支持”提供复制联系方式和作者打赏码。

[许可](LICENSE) · [来源与第三方组件](THIRD_PARTY_NOTICES.md)
