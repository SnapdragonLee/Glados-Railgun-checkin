# GLaDOS 自动签到

基于 GitHub Actions 的 GLaDOS 自动签到方案，支持 `glados.one`、`glados.cloud`、`railgun.info`、多账号、积分兑换、PushDeer 通知和运行状态检查。

改动重点、验证结果及对应 PR 见 [变更日志](CHANGELOG.md)。

## 功能

- 每天北京时间 09:17 和 15:17 自动运行
- 支持 Actions 页面手动运行
- 支持多个 GLaDOS 账号
- 支持 `plan100`、`plan200` 和 `plan500` 积分兑换
- 支持 PushDeer 通知
- Push 和 Pull Request 自动运行单元测试
- 签到结果同步到 GitHub Actions 状态
- 登录设备平台匹配，以及 Actions 失败原因和运行摘要

## 配置

进入仓库的 `Settings` → `Secrets and variables` → `Actions`。

### GLADOS_COOKIES

创建 repository secret `GLADOS_COOKIES`，填写登录后对应域名 `/api/user/checkin` 请求头中的完整 Cookie。会话字段依站点而异，例如：

```text
gld:sess=...; gld:sess.sig=...; koa:sess=...; koa:sess.sig=...;
```

多账号使用 `&` 分隔：

```text
cookie-for-account-1&cookie-for-account-2
```

### GLADOS_EXCHANGE_PLAN

创建 repository variable `GLADOS_EXCHANGE_PLAN`：

| 值 | 兑换条件 |
|---|---|
| `off` | 关闭积分兑换，默认值 |
| `plan100` | 100 积分兑换 10 天 |
| `plan200` | 200 积分兑换 30 天 |
| `plan500` | 500 积分兑换 100 天 |

启用 500 积分兑换时设置为 `plan500`。

### GLADOS_DOMAINS

创建 repository variable `GLADOS_DOMAINS`，填写已验证成功的域名，使用逗号分隔，例如 `glados.one,glados.cloud`。默认运行 `glados.one`。

每个域名先查询登录状态，确认 Cookie 有权限后才签到。多个 Cookie 会依次在配置的域名运行，请使用这些域名对应的有效会话。

首次验证可以在手动运行的 `domains` 输入框填写 `glados.one,glados.cloud,railgun.info`，保持 `exchange_plan=off`，再根据运行摘要选择成功或今日已签到的域名。设备不匹配时，脚本仅按服务端明确返回的 `loginDevice` 匹配已知平台，最多重试一次。

### 可选配置

- repository secret `PUSHDEER_SENDKEY`：PushDeer key
- repository variable `GLADOS_VERBOSE`：`true` 或 `false`，默认 `false`

## 运行

- 定时运行：北京时间 09:17 和 15:17
- 手动运行：Actions → `GLaDOS check-in` → `Run workflow`，默认关闭兑换
- 代码验证：Push 和 Pull Request 自动运行测试
- 排查失败：查看 Actions 的错误注解、运行摘要中的 API code 和脱敏原因
- 积分兑换：成功签到且达到门槛才兑换；今日已签到不兑换，每份 Cookie 每轮最多尝试一次兑换

## 本地测试

```bash
/opt/miniconda3/envs/free/bin/python -m pip install -r requirements-dev.txt
/opt/miniconda3/envs/free/bin/python -m pytest -q
```

## 上游同步

上游更新的审查与同步流程见 [docs/upstream-sync.md](docs/upstream-sync.md)。

## 许可证

本项目使用 GPL-3.0 许可证，见 [LICENSE](LICENSE)。
