# Mesh 前端静态原型

这是 Mesh 工作区的可交互、零运行时依赖静态蓝图。直接双击 `index.html` 即可浏览，不需要安装依赖或启动服务器。

界面字体 Inter 已随原型本地分发，离线打开也不会依赖系统字体或网络。字体采用 SIL Open Font License 1.1，许可文本见 `fonts/OFL.txt`。

## 页面覆盖

- 登录、注册与验证码状态
- 工作区切换、创建与设置
- 收件箱、聊天、My Issues
- Issue 默认看板、Issue 详情、评论与拖拽
- Projects、Agents、Squads、Runtimes
- Skills、Autopilot、Usage 与 Settings
- 空态、加载态、错误态展示
- 桌面与移动响应式布局
- 亮色、暗色与跟随系统主题

## 交互入口

- 点击左侧导航或移动端底栏切换页面。
- 按 `⌘/Ctrl + K` 打开全局命令面板，按 `C` 新建 Issue。
- 看板列头的 `⋯` 可打开列菜单，`+` 可直接新建 Issue。
- 新建 Issue 后可在看板中拖动卡片跨列移动。
- 在 `Settings → Preferences → Theme` 切换 Light、Dark 或 System。
- 点击工作区名称切换或创建工作区。
- 页面路由写入 URL hash，可直接收藏或分享相对入口。

全部界面结构、样式组织、交互脚本、图标与 Mesh 标识均为本目录内的原创实现。

## 浏览器冒烟测试

仓库维护者可用 Chrome DevTools 协议运行交互与安全回归。测试工具依赖在本目录声明并由 `package-lock.json` 锁定；静态原型本身仍无运行时依赖。

先安装锁定的测试依赖：

```bash
npm ci
```

再以普通非 root 用户在两个终端中运行 Chrome 与测试。Chrome 仅监听回环地址，并使用一次性用户目录；退出 Chrome 后临时目录会被删除：

```bash
profile_dir="$(mktemp -d)"
trap 'rm -r -- "$profile_dir"' EXIT
google-chrome --headless=new \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$profile_dir" \
  --disable-extensions \
  --no-first-run \
  "file://$PWD/index.html#/issues"
```

```bash
npm test
```

测试脚本只接受 `http://127.0.0.1:<port>` 的 CDP 端点，并会校验 WebSocket 同源及调试目标必须是本目录的 `index.html`。不要把调试端口暴露到局域网或公网，也不要复用日常浏览器配置。

默认必须保留 Chrome sandbox。若 root CI 环境确实无法启用 sandbox，`--no-sandbox` 只能用于无凭据、隔离网络、一次性销毁的容器；不得在共享开发机或持久化 runner 上使用。
