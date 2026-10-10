# 变更日志

## 2026-10-10 — 恢复签到与多域名验证

PR：[SnapdragonLee/Glados-Railgun-checkin#3](https://github.com/SnapdragonLee/Glados-Railgun-checkin/pull/3)

### 改动重点

- 移植上游提交 [`ee22e8c`](https://github.com/Devilstore/Glados-Railgun-checkin/commit/ee22e8c480950185df095d80f36cf72af4a52c91) 的设备平台匹配修复：仅在 `code=4`、`reason=device-mismatch` 且登录平台已知时追加一次请求。
- 签到使用 JSON 请求；支持三个允许域名，以 `GLADOS_DOMAINS` 配置实际运行列表。
- 每个域名先确认登录状态；认证失败或状态响应不完整时停止该域名的后续请求。
- Actions 显示域名、API code、尝试次数及脱敏失败原因；认证、签到、积分查询和兑换失败均返回非零退出码。
- GET 查询可重试；POST 无通用网络重试。重复 Cookie 去重，每份 Cookie 每轮最多一次兑换尝试。
- 手动验证默认关闭兑换；保留已有兑换门槛和重复签到保护。
- Cookie 及其字段值、邮箱、凭据、URL 和控制字符经过脱敏；请求和解析错误输出异常类型，HTTP 错误输出状态码。

### 实测记录

三个域名使用仓库现有的一份 Cookie 测试，兑换关闭：

| 域名 | 结果 | 运行列表 |
|---|---|---|
| `glados.one` | 签到成功，code=0 | 加入 |
| `glados.cloud` | 今日已签到，code=1 | 加入 |
| `railgun.info` | 认证失败，code=-2，No permission；未发送签到 POST | 当前 Cookie 不适用，暂不加入 |

- [三个域名实测](https://github.com/SnapdragonLee/Glados-Railgun-checkin/actions/runs/38043422872)：51 个测试通过；因 `railgun.info` 认证失败，流程正确退出 1，并生成错误注解和结果摘要。
- [运行列表验证](https://github.com/SnapdragonLee/Glados-Railgun-checkin/actions/runs/38043672440)：`glados.one,glados.cloud` 均返回今日已签到；查询和兑换失败均为 0，完整流程成功。
- 本地 51 个测试通过，`git diff --check` 通过，工作流 YAML 解析通过。
- 本次真实请求未触发设备不匹配恢复；该分支通过合成响应测试。现有证据不能确认过去所有失败的具体原因。

仓库变量 `GLADOS_DOMAINS` 已设置为 `glados.one,glados.cloud`。本次验证后定时工作流仍暂停，待该 PR 合并后使用新代码恢复。
