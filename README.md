# ChatGPT Remote

<img src="WindowsAgent/assets/rm-mark.svg" width="64" alt="Rm">

通过手机浏览器连接电脑上的 ChatGPT，继续查看对话、发送消息和操作窗口。Windows 端常驻 **Rm** 托盘。

当前版本：**v0.16.1**。

- 紧凑主面板直接显示二维码、配对码、连接地址、窗口与设备。
- 点击托盘图标展开，再次点击收起；每次展开回到首页，展开时置顶。
- 312×458 面板、16px 圆角、深浅配色与原生磨砂玻璃，界面结构与设计令牌参考 ChatGPTnx。
- 手机优先显示对话文字，按需切换画面；支持项目和对话切换、回复中排队、手机文件上传。
- 设置提供开机自启、外观、版本更新和联系与支持。

## 下载

[GitHub Releases](https://github.com/LUMIAO9527/ChatGPT-Remote/releases/latest) 提供可运行源码包和离线界面预览。

解压后打开 `ChatGPT-Remote-Preview.html` 可查看同一套界面。预览使用示例设备和配对码。

## 运行

需要 64 位 Windows 10/11、64 位 Python 3.10 或更新版本、PowerShell 7、Microsoft Edge WebView2，以及已打开的 ChatGPT 桌面应用。

在项目文件夹打开 PowerShell 7：

```powershell
.\WindowsAgent\setup_desktop.ps1 -InstallDependencies
```

然后双击生成的 `ChatGPT Remote.lnk`。首次点击主页“启动服务”会安装当前用户的后台任务。

外网连接需要已登录的 Tailscale。先运行 `WindowsAgent/setup_funnel_windows.bat` 配置 8443 入口，再从 Rm 面板扫码；在手机网页输入面板上的配对码。已有配对继续使用原链接。

设备可直接改名、移除；多个窗口直接在主页选择。退出托盘管理程序后远程后台继续运行；服务启停也可使用 `WindowsAgent/manage_background.ps1`。

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

配对、窗口选择和访问密钥保存在 `WindowsAgent/data/`；依赖、运行缓存及手机上传文件位于项目 `_wip/`。更新时保留这两个目录。对话正文不写入程序日志；发布包不包含本机配对、密钥或运行数据。

## 联系与支持

[问题反馈](https://github.com/LUMIAO9527/ChatGPT-Remote/issues) · QQ **210037309**。程序内的“联系与支持”提供复制联系方式和作者打赏码。

[许可](LICENSE) · [来源与第三方组件](THIRD_PARTY_NOTICES.md)
