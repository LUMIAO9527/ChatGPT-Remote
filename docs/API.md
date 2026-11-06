# ChatGPT Remote 1.0.3 Web/Agent API 契约

本文件是 Windows Agent（agent.py）与手机页面（web/index.html）之间的接口契约。
后端按本契约实现；前端只依赖本契约。除本文件列出的字段外，前端不得依赖其他内部结构。

## 0. 通用规则

- 同源访问：页面由 Agent 自己提供（`GET /`），所有 API 走相对路径。
- 公共静态资源：`GET /icon.svg`、`GET /icon.png`、`GET /favicon.ico` 和 `GET /computer-cover.svg` 使用固定资源，不返回设备或会话数据。
- 认证：除 `GET /`、`GET /api/health`、`POST /api/pair` 外，HTTP API 需要
  `Authorization: Bearer <token>`；WebSocket 控制连接在升级后的第一条消息中认证。令牌由配对获得，保存在 `localStorage`。
- 可选外网入口（`--access-key`）：除 `GET /?k=<key>` 外，所有请求还需满足以下之一，
  否则 401：HttpOnly Cookie `hr_access`（由 `GET /?k=` 兑换）或请求头 `X-Access-Key`。
- 外网反向代理（如 Tailscale Funnel）：Host 必须是 `--trusted-host` 配置的域名，
  Origin 必须与 Host 同源（http 或 https）；客户端地址为环回（已允许）。
- JSON 错误响应使用非 2xx 状态，正文为 `{"ok": false, "error": "<中文提示>"}`，可带稳定错误标识 `code`。发送遇到 `draft_exists` 或 `draft_changed` 时，客户端只读核对当前草稿并在输入框旁呈现冲突。WebSocket 错误回复使用同一错误标识。
- 所有 JSON 成功响应：body 至少含 `{"ok": true, ...}`。
- `access_required` 表示入口 Cookie 或访问密钥需要恢复。 `pairing_required` 表示当前设备令牌已失效。入口授权失败时客户端保留设备令牌。
- 时间字段均为 Unix 毫秒（`*_ms`）。
- 局域网默认只允许私有地址；外网访问必须经受信 HTTPS 入口，不要裸端口映射。

窗口模式下使用 `message` 发送，并携带最后观察的 `conversationKey` 与 `epoch`。空输入且原生队列提供 `stop` 时，同一个按钮改为提交 `queue` 停止动作。首次发送前按需读取 `/api/conversation`。目标变化或结果待确认时保留文字，按原消息编号读取回执。

持续文字连接失效时，手机临时轮询 `GET /api/conversation` 并周期性重试增量流。控制 WebSocket 握手或回包超时后自动重建，新操作通过 HTTP 发送。已观察的 `instance` 变化时重新建立窗口版本基线。

手机弹层共用一层页面内历史。首次打开 `pushState`，切换面板 `replaceState`，关闭消费该层历史。`popstate` 恢复主界面或对应面板并保留手机输入。面板打开期间主界面设为 `inert`，焦点在面板内循环，关闭后回到入口按钮。对话选项按需读取，关闭或目标窗口变化后丢弃旧响应。草稿冲突就地显示，切换对话或配对失效时移除预览。

## 1. 端点

### GET /
返回控制台 HTML（`text/html; charset=utf-8`）。

启用访问密钥时：
- `GET /?k=<key>` → `302 Location: /`，并设置
  `Set-Cookie: hr_access=<sha256(key)>; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000`。
- 没有有效 Cookie/`X-Access-Key` 时返回 401 提示页（HTML）或 JSON 错误。

### GET /api/health
无需配对令牌。返回：
```json
{"ok": true, "name": "ChatGPT Remote", "version": "1.0.3", "computerName": "Example-PC", "mock": false, "transport": {"active_streams": 0, "active_controls": 0, "streams": []}}
```
启用访问密钥时仍需有效 Cookie 或 `X-Access-Key`。
`transport` 为当前进程的非持久化传输诊断；每条活动连接仅含活动控制连接数、未确认帧数/字节数、自适应确认上限、已发送字节数和服务器发送画面至收到绘制确认的往返耗时，不包含图片、窗口标题、设备名或令牌。

### POST /api/pair
body：`{"pin": "123456", "name": "手机"}`
`pin` 必须是恰好六位 ASCII 数字（`0`–`9`，保留前导零），仅在 Agent 启动后的 15 分钟内有效。每次启动仅允许注册一台新设备；配对成功后，空码和已用过的码均返回 403，解除配对不会重新开放注册。已有设备令牌继续有效；注册另一台新设备须重启 Agent。错误配对每个来源地址最多 5 次/5 分钟，格式错误同样计数；超过限制返回 429。
成功：`{"ok": true, "token": "<urlsafe token>"}`
失败：400/403/429 + `{"ok": false, "error": "..."}`

### GET /api/windows
认证。返回完整状态：
```json
{
  "ok": true,
  "version": "1.0.3",
  "computerName": "Example-PC",
  "instance": "<本次服务启动编号>",
  "mock": true,
  "windows": [
    {"id": 67584, "title": "ChatGPT", "exe": "ChatGPT.exe", "selected": true}
  ],
  "mode": "window",              // "window" | "desktop"
  "status": "ready",             // 见下表
  "epoch": 123,                  // 目标版本号，整数
  "frozen": {"active": false, "until_ms": 0, "reason": ""},
  "guard": {"active": false, "last_change_ms": 0, "paths": []},
  "capture": {"method": "printwindow", "last_ms": 0}  // 仅诊断，可缺省字段
}
```
`status` 取值与中文含义（前端可自行映射文案）：
- `ready` 已绑定窗口
- `rebound` 刚刚自动重新发现窗口
- `waiting` 原窗口已关闭/未找到，等待新窗口
- `ambiguous` 有多个候选窗口，需要手动选择
- `not_selected` 尚未选择窗口
- `desktop` 完整桌面模式

### POST /api/select
认证。body：`{"id": 67584}`。成功返回与 `GET /api/windows` 相同结构。

### POST /api/desktop
认证。body：`{}`。切换到完整桌面模式，返回同结构（`mode=desktop,status=desktop`）。

### POST /api/window
认证。body：`{}`。从桌面模式切回窗口模式（若已有可用窗口则重新绑定），返回同结构。

### POST /api/freeze
认证。body：`{"frozen": true}` 或 `{"frozen": false}`。
冻结时 bump `epoch`（旧指令作废）；解冻不 bump。返回：
```json
{"ok": true, "frozen": {"active": true, "until_ms": 0, "reason": "manual"}, "epoch": 124}
```
`until_ms=0` 表示手动冻结不会自动解冻；`until_ms>0` 表示到点自动解冻。

### POST /api/action
认证。body 为以下之一（必须带当前 `epoch`）：
- `{"kind":"tap","x":0.42,"y":0.31,"epoch":N}` 左键单击，x/y 为 0~1 归一化坐标
- `{"kind":"right","x":0.42,"y":0.31,"epoch":N}` 右键单击
- `{"kind":"scroll","delta":-240,"epoch":N}` 负数向上、正数向下
- `{"kind":"text","text":"你好","submit":true,"submitMethod":"button","epoch":N}` Unicode 文本，≤6000 字符；`submit` 可省略，默认 false。`submitMethod` 可省略，默认 `button`，也允许 `CTRL+ENTER`、`ENTER`。自动按钮提交要求窗口模式，定位空输入框后填入文字；显式快捷键模式向当前输入焦点注入文字和提交键。
- `{"kind":"message","text":"你好","submit":true,"submitMethod":"button","conversationKey":"...","epoch":N}` 定位当前对话输入框，发送前核对对话标识、输入焦点和编辑器内容。电脑输入框为空时填入手机文字，已有完全一致的文字时可直接提交。不同草稿保留并返回 `draft_exists`。按钮方式在提交前重新检查完整文字，等待唯一可用的发送或排队按钮并调用一次。回复中优先原生按钮，其他时候快捷键方式使用所选 Enter 或 Ctrl+Enter。
- `{"kind":"key","key":"CTRL+ENTER","epoch":N}` 允许：ENTER/CTRL+ENTER/ESC/TAB/BACKSPACE/CTRL+A/CTRL+C/CTRL+V
- `{"kind":"navigate","targetId":"<列表条目id>","navigationKey":"<列表标识>","expanded":true,"epoch":N}` 选择对话、新建对话，或让电脑加载项目。`expanded` 为可选布尔值，默认 true，供项目与分区使用。服务端重新读取当前目录，仅调用曾观察且身份一致的目标；切换或新建对话更新 epoch，项目展开不更新。手机已经加载的项目自行折叠，不发送导航操作。原生确认已经选中的对话返回 `navigationUnchanged:true`，不重新调用或清空现有正文。
成功：`{"ok": true, "epoch": N, "actionSequence": 1, "processingMs": 2.5}`。`actionSequence` 仅用于与后续画面关联；输入结果未确认时客户端不得自动重发。
失败：400（参数）或 409（窗口变化/冻结/目标不可用）+ `error`。

草稿处理另支持 `{"kind":"draft","operation":"clear","conversationKey":"...","draftRevision":"...","epoch":N}`。服务端重新读取并核对对话、完整文字和草稿版本；原生清空前再核对一次，清空后回读确认，不自动重试。

输入框选项支持 `{"kind":"composer","operation":"invoke","optionsKey":"...","targetId":"...","epoch":N}`；`dismiss` 关闭已观察的菜单，可省略 `targetId`；`tap` 附加 `x/y`（0–1，有限数值），仅作用于 `surface` 条目的已核对控件范围。服务端校验 120 秒内观察的目录、窗口、对话身份、弹出层、条目身份及启用状态；实际调用前消费该目录引用，结果未知时不重试。冻结、过期、窗口变化或控件移出目标窗口均拒绝。WebSocket 与 HTTP 使用同一操作入口。

成功操作可附带 `options`（与 `/api/composer` 相同）或 `navigation`（与 `/api/navigation` 相同），用于直接更新当前菜单或项目列表，免除随后一次读取。已确认按钮提交可附带 `submission:{"state":"sent"|"queued"|"submitted"}`；只有 `queued` 才显示已排队。`submitted` 表示已调用原生按钮，仍需回读新增消息或排队项确认。没有确认不得视为成功或自动重试。

### 消息发送回执

手机提交 `kind:"message", submit:true` 时另带 `messageId`（16–80 位字母、数字、下划线或短横线）及 `deliveryInstance`（`/api/windows` 或对话快照的 `instance`）。服务先建立设备绑定的发送任务，立即返回：

```json
{"ok":true,"epoch":123,"delivery":{"id":"send_example_123456789","state":"sending"}}
```

同一设备以相同编号和参数重复请求只返回原回执；编号被复用于不同参数返回 `delivery_conflict`。一次原生提交最多调用一次。UIA 回执丢失后仅回读恢复，既有的相同用户文字不算确认：需新增原生消息身份、相同内容数量增加或新增队列条目，且窗口版本及对话范围一致。首次消息产生新的对话标识属于正常变化。确认后的服务回执移除正文与原始快照，最多保留 256 项完成元信息，最长一小时；待确认任务继续回读。

原生发送先定位可见的主输入框，排除历史消息与队列编辑区；使用选中对话或首条消息正文的原生身份，输入导致的标题变化和消息标题重绘不改变发送目标。重绘期间缺失的控件会重新发现并核对，连续确认不同的对话身份后才拒绝发送。正文一次粘贴后读回核对，保留并恢复原剪贴板，不再逐字注入长消息；输入框与发送按钮的检查仅扫描相关控件，不重读整篇正文。新服务明确拒绝旧 `deliveryInstance` 时，手机保留文字并恢复发送按钮，不等待不存在的回执。

`GET /api/delivery?id=<messageId>`：认证，返回本设备回执。状态为 `sending`、`confirming`、`sent`、`queued`、`failed`；`failed` 附具体 `error/code`。其他设备和不存在的记录返回 404，不返回正文。

`POST /api/delivery/resolve`：认证，body 为 `{"id":"...","instance":"..."}`。已有记录返回原回执。同一次服务启动中尚未收到的编号先封存，再返回 `failed/code:send_not_received`，迟到的同编号提交无法执行，手机保留文字并允许重新发送。启动编号不符时继续观察，不能把旧进程已执行的消息判为失败。

手机把当前待确认编号、原稿与最小消息匹配基线临时保存在本浏览器，完成或解除配对后清除。页面重开只查询该编号，不重新提交。正常对话流已确认时立即结束等待并取消多余回执请求，保留用户新编辑的文字。没有完整匹配基线时等待服务回执，不能用历史中的相同消息确认。

### GET /api/composer

认证。按需读取电脑当前输入区及其菜单：

```json
{"ok":true,"key":"<目录标识>","epoch":123,"available":true,"menu":false,"items":[{"id":"<原生条目>","title":"当前模型","kind":"model","enabled":true,"selected":false}]}
```

`kind` 为 `model`、`permission`、`effort`、`tools`、`option`、`upload`、`surface`、`model-list`、`quick`、`reset` 或 `effort-step`。菜单可附带 `category`，条目可带 `label` 和 `description`。`scope` 是当前原生对话标识的摘要，用于核对同一模型面板内陆续读到的选项。`effort-step` 带 `level`、`currentLevel` 和 `levelCount`。选档只调用已核对且拥有键盘焦点的原生强度控件，操作后核对选中档位。上传条目由手机打开自身文件选择器。条目来自选定窗口的主文档，帮助链接和另有文档身份的嵌入网页不纳入目录。菜单目录只在内存中保留，最多八份。

当前窗口出现可识别的审批或选择弹窗时，`GET /api/composer` 返回 `category: "prompt"` 和 `prompt: {title, text, approval, truncated}`，对话快照也可附带同一份目录。选择使用 `composer/invoke` 及这份目录的 `optionsKey` 和 `targetId`。服务端核对当前窗口、会话、弹窗摘要与按钮，提交后重新读取。手机收起弹层只改变本地界面。

### POST /api/uploads、/api/uploads/attach、/api/uploads/status、/api/uploads/discard

均需配对认证和现有外网访问门禁。`/api/uploads` 接收二进制请求体，`Content-Length` 大于 0 且不超过 25 MB，`X-File-Name` 为 UTF-8 URL 编码的纯文件名；拒绝路径、Windows 保留名称和控制字符。每设备最多 5 个未提交文件、总计 50 MB。返回 `file:{id,name,size,state:"uploaded"}`，不返回磁盘路径或设备标识。文件不进入 JSON、对话轮询或控制 WebSocket。

`/api/uploads/discard` 接收 `{ids:[...]}`，仅允许所属设备移除尚未附加的文件。手机选择后先上传并保留待发送列表，点击发送才向 `/api/uploads/attach` 提交 `{ids:[...],conversationKey,epoch}`。电脑重新核对窗口、对话和输入框焦点，通过一次 CF_HDROP 粘贴附加文件；确认新增附件控件后返回 `state:"attached"`，重复请求返回同一附加结果。未发出粘贴时返回 `code:"attachment_not_started"`，文件保持待发送，可移除或手动重试；已粘贴但回读尚未完成时返回 `state:"attaching"`，客户端使用 `/api/uploads/status` 和相同的 `{ids,conversationKey,epoch}` 自动核验至 `attached`；查询只读取原生附件，保持一次粘贴。尚未附加的上传文件在实际附加时绑定对话；已附加文件保持原对话绑定。随后 `/api/action` 的 `message` 命令可携带 `attachmentIds`，绑定当前对话和窗口且仅用于提交；存在已附加文件时允许空文字。提交仍经过原有草稿与唯一原生发送按钮校验，成功后附件标记已提交。

原剪贴板仅在内存中暂存，仍由本次粘贴占用时恢复；用户期间改动剪贴板则保留新内容。文件临时保存在项目 `_wip/mobile-uploads/`，每 10 分钟清理超过 24 小时的文件；进程重启后只清理符合自身生成目录结构的过期文件。单个上传超时 120 秒；此入口不增加后台 UI 读取。

### GET /api/composer/surface?key=...&target=...

认证。仅对刚观察的 `surface` 条目返回局部控件图像：`{"ok":true,"epoch":N,"key":"...","targetId":"...","image":"data:image/png;base64,...","bounds":{"x":0.02,"y":0.58,"width":0.96,"height":0.34}}`。`bounds` 为图像中可操作区域的归一化边界；点击换算为该区域内坐标后使用 `composer/tap`。图像来自已核对菜单且限定于目标窗口范围，超界拒绝。页面将图像转为内存 Blob 绘制，保留既有内容安全策略；关闭时移除预览，不持久化。

`message` 或窗口模式 `text` 可附加 `draftRevision` 和 `conversationKey`，表示用户明确选择清空已展示的草稿后填入或发送新文字。草稿核对、清空、重新定位和文字输入/提交共用输入锁，不能与其他导航交错。参数、目标、冻结状态和版本不符时不会清空；回复中同样允许经原生按钮提交。若清空或提交结果未知，客户端保留手机文字并要求检查。

### GET /api/draft

认证。窗口模式下主动读取电脑当前草稿，仅保留在本次响应和页面内存中：

```json
{"ok":true,"conversationKey":"<对话标识>","revision":"<草稿版本>","title":"当前对话","text":"尚未发送的文字","hasDraft":true,"truncated":false,"canClear":true,"epoch":123}
```

`revision` 绑定对话身份及完整读取文字。预览最多 32000 字符，截断时 `canClear=false`，客户端不能按部分预览清空。常规对话流只返回 `hasDraft`，不包含草稿正文。读取不保存草稿、不改变输入框。处理请求必须使用该快照的 `epoch`、`conversationKey` 与 `revision`，不能把旧草稿套到新对话；处理结果未知时不得自动重发。

取回或合并只填入手机编辑器。用户明确发送时，客户端携带草稿版本，服务端核对后才替换电脑内容。超过手机 6000 字上限时提供画面入口。清除冲突提示时移除预览。

### GET /api/navigation

认证。窗口模式读取电脑侧栏中可访问的项目与对话：

```json
{"ok":true,"available":true,"epoch":123,"key":"<列表标识>","items":[{"id":"p1","kind":"project","title":"项目","project":"group1","projectTitle":"项目","expanded":true},{"id":"t1","kind":"thread","title":"对话标题","project":"group1","projectTitle":"项目"}],"notice":""}
```

`kind` 允许 `project`、`thread`、`more`、`section`、`sidebar`、`new`。`project` 对应分组标识，`expanded` 在可折叠条目中提供，`selected` 标记已观察到的当前对话。`new` 对应真实可调用的新建按钮，带项目标识时在该项目中打开新对话，没有项目标识时使用全局入口；不发送消息。`source` 为窗口根身份的散列。列表最多返回 1000 个条目；筛选由前端对已加载条目完成，加载更多通过 `more` 导航条目进行。未提供可读取目录时返回 `available=false` 与 `notice`；桌面模式或窗口不可用返回 409。

目录与条目仅在内存保留，服务端保留最多 32 个观察快照；不按短期计时淘汰，操作时重新核对窗口根、条目 id、类型、标题及项目身份。手机按窗口来源保留已加载子列表及各自目录引用，切换对话后保留列表并更新窗口版本。已加载项目开合、重开面板不产生请求；尚未加载的项目首次展开才请求。新对话首次发送后按需更新列表，显式刷新会清除当前未加载项目的旧子列表。窗口或账号变化使手机目录失效；服务端失去观察快照或目标变化时要求刷新。导航通过 HTTP 提交一次。回执丢失时，手机只读核对所选对话。读取正文时遇到版本切换会重新同步。

### POST /api/unpair
认证。body：`{}`。吊销当前令牌。成功：`{"ok": true}`

### GET /api/frame
认证。JPEG 画面。响应头：
- `X-Frame-Id`: 单调递增帧号（整数）
- `X-Target-Epoch`: 该帧对应的目标版本号
- `X-Captured-Ms`: 抓取时间 Unix 毫秒
- `X-Capture-Method`: `printwindow` / `screen` / `mock`
- `X-Action-Sequence`: 本次抓取开始前已完成的操作序号

两种用法：
1. 不带查询参数：立即抓取一帧并返回。目标不可用时 409。
2. `?since=<lastFrameId>`：长轮询。有新帧立即返回 200；等待约 1.8 秒仍无新帧返回
   `204 No Content`（无 body）；目标不可用返回 409。前端收到 204 应立即再次请求。

### GET /api/stream
认证与 Host/Origin 校验与其他 API 一致。响应为 `application/octet-stream`，HTTP chunked 持续响应；不在 URL 中携带设备令牌。每个应用层包为：

```
uint32 big-endian: UTF-8 JSON 元数据长度（2..8192）
uint32 big-endian: 图片字节长度（0..4 MiB）
JSON 元数据
图片字节
```

HTTP 分块和应用层包边界互相独立，客户端必须处理拆包和粘包。

- `kind=full`：完整 JPEG；首次连接、尺寸或 `epoch` 变化时发送。字段含 `frameId`、`epoch`、`capturedMs`、`method`、完整画面的 `width/height`、`actionSequence`、`streamId`。
- `kind=patch`：变化区域的 PNG，另含左上角 `x/y`。按序叠加到同一尺寸、同一 `epoch` 的完整画布上；不能跳过补丁。
- `kind=idle`：连接保活，无图片字节。
- `kind=status`：目标不可用，`error` 提供提示，随后断开；重连从完整画面开始。

图片无显著变化时不发送图像。确认窗口初始三张，按确认往返耗时自适应至 3–12 张；未确认字节达到 256 KiB 时也暂停发送（单张图片发送后可能越过该阈值）；恢复后取最新截图，差分基于最后实际发送的画面。8 秒未收到有效进度确认时结束堵塞的连接。令牌吊销后流关闭。

### POST /api/stream/ack
认证。body：`{"streamId":"<当前连接编号>","frameId":N}`。绘制完毕后确认，可合并确认到最新已绘制编号；只能确认本设备的连接，不能确认尚未发送的编号。成功返回 204，参数错误 400、设备不匹配 403、连接已结束 404。
浏览器优先通过 WebSocket 控制连接发送确认。HTTP 回退最多同时发送两条确认请求，其余进度累计合并；新确认不必等待上一条请求响应。较旧确认晚到时不撤回已确认进度。切换流后不再发送旧流的后续确认。

### GET /api/control（WebSocket）

同源升级为 RFC 6455 WebSocket。升级前检查访问密钥 Cookie、Host 和 Origin；升级后 5 秒内发送 `{"type":"auth","token":"<设备令牌>"}`，成功返回 `{"type":"auth","ok":true}`。令牌不放入 URL。失败返回 401 状态字段并关闭。

认证后每条请求携带单调整数 `id`：

- 操作：`{"type":"action","id":1,"payload":{...与 /api/action 相同...}}`
- 画面确认：`{"type":"ack","id":2,"streamId":"...","frameId":123}`
- 保活：`{"type":"ping","id":3}`

回复为 `{"type":"reply","id":1,"ok":true,"data":{...}}`，或 `{"type":"reply","id":1,"ok":false,"status":409,"error":"..."}`。服务按接收顺序执行。客户端每 10 秒保活，控制连接空闲 30 秒关闭。客户端消息必须遮罩，UTF-8 JSON 对象总长不超过 64 KiB，支持分片及 ping/pong。关闭或确认超时后，不自动重发已发送的操作。控制连接不可用时 HTTP 操作串行处理，避免乱序。

### GET /api/conversation

认证。返回当前窗口对应的对话快照。已识别的本机线程使用只读结构化历史，返回 source="local-history"、threadId 与 loadedOnly=false；其它界面读取可访问消息：

```json
{"ok":true,"available":true,"key":"<对话标识>","revision":"<快照标识>","title":"当前对话", "epoch":123,"status":"ready","mode":"window","frozen":{"active":false,"until_ms":0,"reason":""},"messages":[{"id":"m0","role":"user","text":"问题"},{"id":"m1","role":"assistant","text":"回答"}],"composer":{"available":true,"hasDraft":false},"generating":false,"loadedOnly":true,"truncated":false,"notice":""}
```

正文优先从已识别线程的原始本机消息记录读取，消息 id 与 sourceId 使用原始消息编号。侧栏和输入框草稿不作为消息返回；`hasDraft` 仅表明电脑存在草稿，不返回草稿内容。最多保留最近 80 段、总量约 160000 字符；截断时 `truncated=true`。`key` 优先使用窗口、主文档与选中对话行的标识；侧栏不提供该行时使用首条消息体，最后回退到标题与角色标记。完整读取和发送前检查采用相同的元信息发现规则，文字去重只抑制重复内容，不跳过控件。它用于阻止切换对话后的旧指令，不替代账号认证。未提供可识别结构时 `available=false`，`notice` 提示切换画面。桌面模式或窗口不可用返回 409。

编辑器的原生 placeholder 子树、纯空白和格式占位符不作为草稿；判断与发送按钮启用状态分开，用户真实输入同名文字仍保留。实际消息的界面时间可作为可选 `timestamp` 字段返回，前端放在正文外的消息栏；正文中用户自己写下的时间保持原样，复制内容不含界面时间。

每条消息可另含 `blocks:[{"kind":"paragraph","text":"正文"}]`，`kind` 允许 `paragraph`、`list`、`heading`、`code`、`search`。同一原生段落容器的片段合并，分别保留不同容器的段落与代码块；列表保留完整项目，包括只存在于项目名称中的文字。块可含 `runs:[{text,strong?,emphasis?,inlineCode?,href?}]`；链接只接受无用户名密码的 HTTP(S) 地址，文字始终通过安全 DOM 渲染。实际格式取决于应用提供的结构。`text` 保留纯文本复制来源。前端用安全 DOM 元素和 `textContent` 排版；没有可用 `blocks` 时按纯文本段落显示。

块可附带 `channel:"reasoning"|"activity"`，缺省为正文；消息可附带 `phase:"activity"|"reasoning"`。活动消息按原始轮次合为一个可折叠区域，内部公开思考摘要降低字号和亮度；`search` 块单独展示网页搜索标识。消息 `timestamp` 单独在底部显示。分类优先使用原生消息类型、phase 和公开思考 summary；兼容读取使用主文档原始可访问性容器。连续活动消息合并为同一折叠区。

消息含 `complete`，回复生成中隐藏复制图标，结束后显示；思考和活动消息不单独显示复制。`attachments:[{id,kind:"image"|"file",name?}]` 只传元数据，可保留无文字的附件消息。图片合并为张数标签，不提供图片下载或预览，不另加轮询。

结构化历史中的消息保留 `turnId`。同轮活动携带 `work:{status:"working"|"worked"|"stopped"|"unknown",durationMs?:number}`；耗时来自原始轮次，不放入正文。手机按原生界面显示“处理中”“用时”或停止状态，活动与耗时共用一个入口；无详情时只显示状态，不创建空的折叠控件。

消息可附带 `duration`，例如“已处理 1分钟 27秒”。识别同时核对耗时格式和原始可访问性容器，结果在正文上方作为原生状态显示；它不进入 `text` 或 `queued`，普通正文里的同名文字不受影响。暂时没有助手正文可关联时，返回空正文、`phase:"activity"` 的助手状态行。时间支持星期前缀，耗时依据原始容器识别；普通段落中的同名文字仍作为正文。

快照与增量事件另含 `queued:[{"id":"...","text":"待发送用户文字"}]`，这是完整的当前可读取队列，不能并入上一条 assistant 消息。手机在输入框上方单行省略显示，点击展开，消费后随下一次同步移除。按钮确认的本地提示在原生队列或用户消息回读后合并去重，未回读的临时提示最多保留 15 秒；队列和正文共用现有流，不另加请求。

项目目录可含 `complete:true`，表示当前返回目录中的项目已完成本地读取；空项目可直接显示空状态。缓存项目的展开不受发送或后台目录读取阻塞；同一窗口的状态版本改变时保留缓存并校对目录，实际切换对话使用校对后的目标。

`composer.options` 可包含与 `/api/composer` 相同的已观察根入口（不含 `epoch` 时使用外层事件的 `epoch`）。手机可直接显示，在实际选择时仍验证原生目标。入口缓存最多 120 秒；项目目录在当前手机会话中复用，窗口或账号变化时失效。面板打开期间暂停正文和图片流，关闭后恢复。

### GET /api/conversation/stream

与快照接口相同认证。`application/x-ndjson; charset=utf-8`、HTTP chunked 持续响应，每一行是一个 JSON 事件；读取轮次间隔约 650ms，无变化时约 1.5 秒保活。

`kind=conversation` 除快照字段外还含 `replace` 和 `order`；`messages` 只包含变化或新增加的段落。`replace=true` 时清空旧列表，`order` 给出全部保留消息的顺序，删除不在其中的旧消息。对话 key 或 epoch 变化时重新发送完整快照。`kind=idle` 表示保活；`kind=status` 表示窗口变化，客户端清空旧列表并重连。正文只在服务进程及手机页面内存中保留，不写日志或缓存文件。

### GET /api/queue 与原生队列操作

认证后按需读取当前窗口的原生队列。返回 key、epoch、items、actions、paused 和 hasDraft。每个条目含不透明 id、完整可读取文字 text 及当前原生控件提供的 actions；每个操作含 id、operation 和 enabled。条目操作为 steer、edit、delete，全局操作为 resume、stop，只展示实际观察到的入口。

操作通过现有 /api/action 或控制连接提交 {"kind":"queue","queueKey":"...","targetId":"...","epoch":N}。服务核对目录时效、窗口、原生对话身份、条目全文、控件身份及启用状态，消费操作引用后只调用一次。删除和引导需要回读条目减少；继续和停止需要回读原生状态。结果未确认返回 queue_pending，客户端只刷新和查看，不重复操作。

编辑调用原生队列菜单，返回真实输入框的 queueDraft（与草稿接口相同字段），原附件由桌面原生编辑状态持有。手机已有输入或附件时先保留当前内容；电脑已有草稿时拒绝进入队列编辑。后续发送带返回的草稿版本，复用原有受保护的草稿替换与原生提交。此接口不写原始对话或队列数据库。

公开 commentary 消息作为独立过程说明保留；公开思考摘要和活动继续折叠。内部子任务按原始 source 过滤，当前窗口提供的原生对话入口继续保留。同一对话的完整快照按消息编号对齐，保留阅读位置。

## 2. 前端必须实现的行为

1. 配对页：输入 6 位数字（`inputmode=numeric`，自动聚焦），设备名默认“手机”。
   成功后保存 token 到 `localStorage`（key：`chatgpt-harmony-demo-token`），进入当前对话；
   已有 token 时进入对话或恢复画面偏好，清理旧的二级页面标记（若 API 返回 401 则回到配对页并清除 token）。设置、项目和选项使用页面片段加入历史，返回继续当前对话。电脑连接页是二级界面，暂停重型读取，保留输入、附件和队列。
2. 两种视图共用顶栏、查看切换、项目入口、按钮和底部输入框。默认对话文字，消息区独立滚动；窗口选择、发送方式、快捷键、冻结和解除配对收进“设置”。“项目”打开目录并支持展开、收起、更多与筛选；切换期间暂停发送。切到画面时启动图片流，返回对话时关闭图片流。对话通过 `textContent` 渲染，不将电脑正文作为 HTML 执行。手机回车换行、Ctrl+Enter 提交；电脑端提交方式保存在本地浏览器（key：`chatgpt-harmony-submit-method`）。
3. 画面模式读取 `/api/stream`，按序绘制到固定 Canvas 并确认；连接恢复时从完整画面开始。服务器或浏览器不支持时才使用 `/api/frame` 兼容接口。显示最近变化画面的更新率，以及在本浏览器用 `performance.now()` 测得的请求往返耗时；不把两台设备的时钟相减作为网络延迟。8 秒未完成网络响应或收到完整画面包时取消请求并重连。
4. 画面操作门槛：仅当 `displayedEpoch === epoch` 时允许操作；否则提示“窗口正在切换”。对话发送要求最新对话快照的 epoch 一致且连接新鲜；服务端再次检查对话 key、草稿和输入焦点。
   收到 409 时自动刷新窗口状态。
5. 触控：轻点=左键；长按 ≥550ms=右键；上下拖动=滚动；滚轮=滚动。
   坐标换算必须考虑图片 contain 留黑边（letterbox）。
6. 窗口切换：`GET /api/windows` 每 5 秒后台同步一次，后台同步不改变“刷新”按钮；手动点击时才显示进度。画面 `epoch` 变化或 409 会立即触发状态同步；
   `status` 为 `waiting/ambiguous/not_selected` 时禁用画面操作并显示提示；
   `ambiguous` 时要求从下拉框手动选择。
7. 冻结：按钮切换 `POST /api/freeze`，冻结时显示醒目横幅并禁用输入操作。
8. 完整桌面模式：可切换；显示“完整桌面”标记，并提供“返回窗口模式”。
9. 全屏查看：画面卡片可进入全屏样式（CSS 覆盖层即可，退出按钮可见）。
10. 不加载任何外部资源（无 CDN/字体/图标）；CSP 允许 inline script/style。
11. 手机浏览器与桌面浏览器共用网页；已覆盖 ArkWeb/Chromium 的适配，支持横竖屏。项目标题栏和项目行使用相同的新建图标，保持 44×44 点击范围、可访问名称及长按说明。搜索框下方间距为 8 像素，筛选栏下方为 4 像素；空的错误状态不占位。
12. 所有提示为简体中文。

## 3. 验收用例（前端自测）

- 无 token → 配对页；错误 PIN → 红色错误提示；正确 PIN → 控制页。
- 读取当前窗口已加载的文字、增量回复与复制；切换对话后清理旧列表。
- 直接发送中文消息。生成中按桌面实际能力提交到队列。电脑草稿可取回到输入框，两端冲突时可以合并或明确替换后发送。对话已切换时阻止旧指令。
- 选择窗口后能点按画面、上下拖动滚动、发送中文文本，并按所选方式提交；结果未知时禁止自动重发。
- 冻结后操作被拒绝并显示原因；解冻恢复。
- 模拟窗口重启（后端 `simulate_restart`）后，新画面携带新的 `epoch`，触发状态同步并提示“已自动重新发现”；恢复耗时须按实际链路测量。
- 204 长轮询不会导致页面闪白或控制台报错。


## 本机桌面管理（v0.15.0）

桌面管理使用 `WindowsAgent/desktop_control.py` 的 Windows 命名管道，手机 HTTP 路由不提供管理入口。每个数据目录有独立管道，管理密钥用当前 Windows 用户的 DPAPI 加密保存在 `data/desktop_control.dat`。请求和响应使用 HMAC、时间戳、随机编号及重放检查，只传递 JSON 字节。

`status` 返回服务、窗口和设备元信息，省略访问密钥、配对码及对话正文。`pair` 和 `access_link` 在桌面明确操作时返回配对信息；设备重命名和撤销使用不含原始令牌的设备编号。桌面页面通过有限动作桥调用管理器。

后台任务的 `AutoStart -Enabled 0/1` 同时管理登录和分钟恢复触发器。`Stop` 关闭分钟恢复并停止服务，保留登录自启偏好；`Start` 恢复与偏好相符的触发器。计划任务身份为当前用户的交互式会话。


## 托盘运行结构（v0.16.1）

`desktop_tray.py` 是适配自用户自有 ChatGPTnx v1.6.0 的原生通知区域组件，处理 Explorer 重建与注册重试。`desktop_platform.py` 管理 312×458 逻辑像素面板的 DPI 换算、停靠、展开时置顶、UI 线程排队和失焦确认。`desktop_app.py` 的托盘与界面分进程，使用单实例锁及具备最新可见状态的命名事件通信。图标的 SVG 与 ICO 共用 `desktop_icon.py` 的 Rm 矢量路径。

托盘启动后预先创建隐藏界面，收起后继续保留。`window.page` 只更新页面标识，不调整原生窗口的尺寸或位置。`window.ready` 不等待后台状态读取；隐藏界面停止状态与配对倒计时轮询，清除配对码及未完成配对请求的显示资格。每次展开回到主页；离开设置后清除展开状态，再次进入时全部收起。退出管理程序会关闭界面进程，远程后台服务继续独立运行。

`ChatGPTRemote-Tray` 为当前用户的独立交互式登录任务，和 `ChatGPTHarmonyRemote-Agent` 分开托管。自启开关同时设置两者的登录触发器；停止后台不会结束托盘。`window.page` 只接受已定义页面；`window.interaction` 只接受布尔状态。桌面桥不提供任意系统命令。

`window.appearance` 只接受已解析的浅色或深色，以及当前选择的磨砂玻璃或纯色材质，由原生 UI 线程即时应用。外观包含系统/浅色/深色配色及磨砂玻璃/纯色材质，设置写入串行处理，较旧的读取不能覆盖新的选择。原生区域、玻璃裁剪与页面圆角统一为 16 逻辑像素半径。

界面进程接收托盘窗口句柄与通知图标编号，每次展开重新调用 `Shell_NotifyIconGetRect`。定位使用目标显示器 DPI；临时无法读取图标时使用当前点击位置。主窗口区域直接裁剪，玻璃由 `native_backdrop.cpp` 的 C++/WinRT 宿主按同一几何范围合成。

`updates.check` 异步读取 GitHub 最新正式版的元数据。`updates.status` 单独读取检查和下载状态，版本更新页可在手机服务停止时使用。后台检查成功后六小时再查，连接失败后十五分钟重试。独立程序通过 `updates.download` 下载并校验安装包，再通过 `updates.install` 启动可回退的更新。源码运行提供正式发布页入口。`links.open` 只接受项目、问题反馈和发布页三个固定入口。`support.copy` 复制公开 QQ 号。

普通消息文字不依赖系统剪贴板；较长文字优先粘贴，剪贴板不可用且尚未调用原生输入时自动选择 Unicode 输入。该选择只发生在输入开始前，发送按钮始终按原有回读确认与单次调用规则处理。
