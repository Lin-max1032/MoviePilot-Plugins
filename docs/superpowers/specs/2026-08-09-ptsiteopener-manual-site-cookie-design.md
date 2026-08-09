# PTSiteOpener 手动站点 Cookie 配置

## 背景

PTSiteOpener 当前优先复用 MoviePilot 站点管理中的 `Site.cookie`。部分通过 API 连接的站点不会在站点管理中保存 Cookie，导致插件无法为远程 CDP 浏览器恢复这些站点的登录状态。

## 目标

- 在插件配置页提供手动填写指定站点 Cookie 的入口。
- 使用站点名称匹配手动 Cookie。
- 只有站点管理中的 Cookie 没有有效键值对时，才回退使用手动 Cookie。
- 保持现有“复用站点 Cookie”开关作为总开关。
- Cookie 值不得出现在日志、告警或通知中。

## 配置格式

新增配置项 `manual_site_cookies`，使用多行文本保存，每行格式为：

```text
站点名称:Cookie
```

示例：

```text
朱雀:socute=s%3A15cef7de049d4887b69df70a26609672.m519IoHR45kaRCmMiHwuIlETy7HJK9IHNMYUJkDbRo4
```

解析规则：

- 空行忽略。
- 以第一个 `:` 分隔站点名称和 Cookie，因此 Cookie 值中的 `:`、`=` 不会被截断。
- 站点名称和整行 Cookie 两端空白去除；Cookie 内部字符保持原样。
- 缺少分隔符、站点名称为空或 Cookie 无有效 `name=value` 键值对的行忽略并记录告警。
- 相同站点名称重复配置时，后出现的有效配置覆盖前一条。

## Cookie 选择优先级

每个启用站点打开前按以下顺序选择 Cookie：

1. 如果 `reuse_site_cookie` 关闭，不注入任何 Cookie。
2. 解析站点管理的 `Site.cookie`。只要存在有效键值对，就使用该 Cookie。
3. 如果站点管理 Cookie 为空或没有有效键值对，使用 `Site.name` 精确匹配的手动 Cookie。
4. 两者都没有有效 Cookie 时，沿用当前无 Cookie 的直接打开流程。

手动 Cookie 不会覆盖站点管理中已有的有效 Cookie。

## 实现边界

- 在 `plugins.v2/ptsiteopener/__init__.py` 增加手动 Cookie 解析和按站点选择的辅助函数。
- 配置初始化时解析 `manual_site_cookies`，无效行只产生告警，不使整个插件失效。
- 在现有 `Network.setCookie` 注入流程前选择 Cookie 来源，CDP 打开、关闭标签页和失败告警流程保持不变。
- 配置页增加 `VTextarea`，模型为 `manual_site_cookies`，提供格式示例和较大的输入区域。
- 插件版本更新为 `1.3.0`，`package.v2.json` 和 README 同步更新。

## 错误处理与安全

- 手动配置解析失败时记录站点名称或行号等定位信息，但不记录 Cookie 内容。
- CDP 注入失败时沿用现有告警机制；日志和通知只包含站点、地址和脱敏后的错误原因。
- 手动 Cookie 无效不会阻止对应站点继续打开。

## 测试范围

- 解析 `站点名称:Cookie` 时保留 Cookie 中的 `:` 和 `=`。
- 忽略空行、缺少分隔符和无有效键值对的行。
- 站点管理 Cookie 有效时优先于手动 Cookie。
- 站点管理 Cookie 为空时使用名称匹配的手动 Cookie。
- 关闭 `reuse_site_cookie` 时手动 Cookie 也不注入。
- 配置页包含手动 Cookie 输入项，默认值为空，插件和市场版本为 `1.3.0`。
- 手动 Cookie 注入失败时不泄露 Cookie 值，并且站点仍会继续打开。
