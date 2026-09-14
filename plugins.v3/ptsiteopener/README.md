# PT 站点定时访问（V3）

这是一个 MoviePilot V3 插件。它按用户配置的 Cron 计划，通过远程 Chrome DevTools Protocol 定时打开 MoviePilot 中已启用的 PT 站点，复用站点 Cookie，并在保留时间到期后关闭插件创建的标签页。不读取 Chrome 书签。

本插件是 `plugins.v2/ptsiteopener` 的 V3 独立实现，业务规则与 V2 保持一致；站点读取使用 V3 稳定端口（`app.db.oper.site.SiteOper`），动态 API 由宿主生命周期注册，依赖通过 `pyproject.toml` 声明后由宿主安装。

## 配置

- `远程 CDP 地址`：默认留空。填写 MoviePilot 可访问的 HTTP/HTTPS CDP `/json/version` 地址；为空时可以保存配置，但计划任务或立即执行会提示“未配置远程 CDP 地址”。
- `开启通知推送`：开启后，计划执行和手动执行完成时通过 MoviePilot 通知渠道推送结果，默认关闭。
- `复用站点 Cookie`：默认开启。打开站点前使用 MoviePilot 站点管理中保存的 Cookie，以便复用站点登录状态。
- `手动站点 Cookie`：每行填写一个 `站点名称:Cookie`，例如 `朱雀:socute=s%3A...`。仅当对应站点在站点管理中没有有效 Cookie 时使用；Cookie 中的 `:` 和 `=` 会保留。
- `执行周期`：点击输入框弹出五段 Cron 表达式编辑器，默认 `0 */6 * * *`，即每 6 小时执行一次。
- `标签页保留时间`：默认 5 分钟，到期后只关闭本插件本次创建的标签页。
- `站点范围`：默认打开全部启用站点，也可以切换为指定启用站点。
- `立即执行`：不等待 Cron，直接执行一次当前配置的站点打开任务，结果返回宿主统一响应结构（`opened` 列表位于 `data` 段）。

Cookie 选择顺序为：先使用站点管理中的有效 Cookie，再回退到站点名称匹配的手动 Cookie。关闭 `复用站点 Cookie` 后两种 Cookie 都不会注入。手动配置中的无效行会记录告警但不会阻止站点打开。Cookie 注入失败时会记录 MoviePilot 告警日志；开启通知推送后，还会发送站点、地址和失败原因通知，但不会输出 Cookie 内容。

## 依赖

`websocket-client` 由 `pyproject.toml` 声明，宿主在安装插件时自动安装；`apscheduler`、站点数据读取与插件基类由 MoviePilot V3 宿主提供。