# GLaDOS 自动签到（加固 Fork）

本分支基于 `Devilstore/Glados-Railgun-checkin`，面向自己的 GitHub Fork 运行。它只访问 `https://glados.one`，默认关闭积分兑换，并让 GitHub Actions 的成功/失败状态真实反映签到结果。

## 与上游版本的关键差异

- Cookie 只会发送给 `glados.one`；拒绝旧域名和 HTTP 重定向。
- 签到失败后立即停止该账号的积分查询与兑换。
- 自动兑换默认关闭；积分查询成功且达到阈值时才请求兑换。
- `签到成功` 和 `今日已签到` 退出 0；签到失败或应兑换但兑换失败退出非 0。
- 服务端错误响应正文不会进入 Actions 日志。
- Actions 使用只读权限、并发锁和固定 SHA；移除了 keepalive、自动删除运行记录等无关步骤。
- PushDeer 是可选通知，通知失败不会覆盖签到的真实结果。

## 配置 GitHub Actions

在自己的仓库进入 `Settings` → `Secrets and variables` → `Actions`。

### 必需 Secret

创建 `GLADOS_COOKIES`，值为 GLaDOS 请求中的完整 Cookie header，例如：

```text
koa:sess=...; koa:sess.sig=...;
```

多账号继续使用 `&` 分隔：

```text
cookie-for-account-1&cookie-for-account-2
```

Cookie 等同于登录凭据。不要把它提交到 Git、复制到 Issue，或粘贴到 Actions 日志。

### 自动兑换

创建 repository variable `GLADOS_EXCHANGE_PLAN`。可选值：

| 值 | 行为 |
|---|---|
| `off` | 关闭自动兑换（默认） |
| `plan100` | 达到 100 积分后兑换 10 天 |
| `plan200` | 达到 200 积分后兑换 30 天 |
| `plan500` | 达到 500 积分后兑换 100 天 |

要启用 500 积分兑换，明确设置为 `plan500`。脚本会先读取积分，只有 `points >= 500` 才发送一次兑换请求。Actions 的 `concurrency` 会避免定时与手动运行并发。

### 可选配置

- Secret `PUSHDEER_SENDKEY`：PushDeer key；不配置则只看 Actions 结果。
- Variable `GLADOS_VERBOSE`：`true` 或 `false`，默认 `false`。

## 运行时间与触发规则

- 定时：UTC 01:17 / 07:17，即北京时间 09:17 / 15:17。
- 手动：Actions 页面运行 `GLaDOS check-in`。
- Push / Pull Request：只运行单元测试，不读取 Cookie，也不进行真实签到。

第二次定时运行是补偿任务；若当天已签到，脚本把 `今日已签到` 视为成功。

## 本地测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

不要用真实 Cookie 运行测试。测试使用假 API，不访问 GLaDOS。

## 分支与上游同步

本仓库把原项目记为 `upstream`，自己的 GitHub Fork 记为 `origin`。我们自己的功能提交保留在独立分支中；上游变化先审查，再按需要 merge 或 cherry-pick，不自动覆盖本分支。

完整流程见 [docs/upstream-sync.md](docs/upstream-sync.md)。

## 许可证

沿用上游项目的 GPL-3.0 许可证，见 [LICENSE](LICENSE)。
