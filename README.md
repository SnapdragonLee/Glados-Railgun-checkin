# GLaDOS 自动签到

基于 GitHub Actions 的 GLaDOS 自动签到方案，使用 `https://glados.one`，支持多账号、积分兑换、PushDeer 通知和运行状态检查。

## 功能

- 每天北京时间 09:17 和 15:17 自动运行
- 支持 Actions 页面手动运行
- 支持多个 GLaDOS 账号
- 支持 `plan100`、`plan200` 和 `plan500` 积分兑换
- 支持 PushDeer 通知
- Push 和 Pull Request 自动运行单元测试
- 签到结果同步到 GitHub Actions 状态

## 配置

进入仓库的 `Settings` → `Secrets and variables` → `Actions`。

### GLADOS_COOKIES

创建 repository secret `GLADOS_COOKIES`，填写 GLaDOS 请求中的完整 Cookie：

```text
koa:sess=...; koa:sess.sig=...;
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

### 可选配置

- repository secret `PUSHDEER_SENDKEY`：PushDeer key
- repository variable `GLADOS_VERBOSE`：`true` 或 `false`，默认 `false`

## 运行

- 定时运行：北京时间 09:17 和 15:17
- 手动运行：Actions → `GLaDOS check-in` → `Run workflow`
- 代码验证：Push 和 Pull Request 自动运行测试

## 本地测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 上游同步

上游更新的审查与同步流程见 [docs/upstream-sync.md](docs/upstream-sync.md)。

## 许可证

本项目使用 GPL-3.0 许可证，见 [LICENSE](LICENSE)。
