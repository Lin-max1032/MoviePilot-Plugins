# PT 站点定时访问

这是一个 MoviePilot V2 插件。它按用户配置的 Cron 计划，通过远程 Chrome DevTools Protocol 定时打开 MoviePilot 中已启用的 PT 站点，复用站点 Cookie，并在保留时间到期后关闭插件创建的标签页。不读取 Chrome 书签。

## 配置

- `远程 CDP 地址`：默认留空。填写 MoviePilot 可访问的 HTTP/HTTPS CDP `/json/version` 地址；为空时可以保存配置，但计划任务或立即执行会提示“未配置远程 CDP 地址”。
- `开启通知推送`：开启后，计划执行和手动执行完成时通过 MoviePilot 通知渠道推送结果，默认关闭。
- `复用站点 Cookie`：默认开启。打开站点前使用 MoviePilot 站点管理中保存的 Cookie，以便复用站点登录状态。
- `手动站点 Cookie`：每行填写一个 `站点名称:Cookie`，例如 `朱雀:socute=s%3A...`。仅当对应站点在站点管理中没有有效 Cookie 时使用；Cookie 中的 `:` 和 `=` 会保留。
- `执行周期`：点击输入框弹出 MoviePilot V2 Cron 周期计算器，生成并保存 5 段 Cron 表达式，默认 `0 */6 * * *`，即每 6 小时执行一次。
- `标签页保留时间`：默认 5 分钟，到期后只关闭本插件本次创建的标签页。
- `站点范围`：默认打开全部启用站点，也可以切换为指定启用站点。
- `立即执行`：不等待 Cron，直接执行一次当前配置的站点打开任务。

Cookie 选择顺序为：先使用站点管理中的有效 Cookie，再回退到站点名称匹配的手动 Cookie。关闭 `复用站点 Cookie` 后两种 Cookie 都不会注入。手动配置中的无效行会记录告警但不会阻止站点打开。Cookie 注入失败时会记录 MoviePilot 告警日志；开启通知推送后，还会发送站点、地址和失败原因通知，但不会输出 Cookie 内容。

示例：

```text
0 8,20 * * *
```

表示每天 08:00 和 20:00 执行。

## 依赖

插件目录中的 `requirements.txt` 需要安装 `websocket-client`。`apscheduler`、MoviePilot 数据库和插件基类由 MoviePilot V2 宿主提供。
