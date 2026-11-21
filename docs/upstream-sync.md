# 上游同步流程

目标是同时保留两条清晰历史：

- `upstream/master`：原项目的提交；
- `codex/harden-glados-checkin`（或后续自有分支）：我们的加固提交。

## 远端约定

```bash
git remote -v
# origin    git@github.com:<you>/<your-fork>.git
# upstream  https://github.com/Devilstore/Glados-Railgun-checkin.git
```

在自己的 GitHub Fork 创建后添加它：

```bash
git remote add origin git@github.com:<you>/<your-fork>.git
```

`upstream` 只用于 fetch。不要直接向上游推送。

## 每次同步前的审查

```bash
git fetch upstream --prune
git log --oneline --decorate HEAD..upstream/master
git diff --stat HEAD...upstream/master
git diff HEAD...upstream/master -- checkin.py .github/workflows requirements.txt
```

重点审查：

1. 是否新增域名、重定向或 Cookie 输出；
2. 是否改变签到/兑换 API、积分阈值或退出码；
3. workflow 是否扩大权限、加入第三方 Action、改变 Secrets 作用域；
4. 依赖或 Action 的版本更新是否有明确来源；
5. 是否会绕过现有测试或并发锁。

## 合并方式

上游的一组提交需要整体保留时：

```bash
git switch codex/harden-glados-checkin
git merge --no-ff upstream/master
```

只需要少量独立修复时：

```bash
git switch codex/harden-glados-checkin
git cherry-pick <reviewed-upstream-commit>
```

解决冲突后必须重新运行：

```bash
/opt/miniconda3/envs/free/bin/python -m pytest -q
git diff --check
```

确认测试和 diff 后，才推送到自己的 `origin`。不要使用 `git reset --hard` 追平上游，因为那会丢弃自己的提交历史。

## 已移植的设备匹配逻辑

签到设备平台匹配逻辑参考上游提交 `ee22e8c480950185df095d80f36cf72af4a52c91`（2026-09-29）。仅在服务端返回 `code=4`、`reason=device-mismatch`、已知 `loginDevice` 时匹配平台，并最多重试一次。

本 fork 保留签到 JSON 请求、运行域名配置、Cookie 接收方白名单、拒绝重定向、仅 GET 网络重试、兑换门槛和业务失败非零退出码。每份 Cookie 每轮最多尝试一次兑换。

本次集成见 [PR #3](https://github.com/SnapdragonLee/Glados-Railgun-checkin/pull/3)，域名实测与运行列表记录见 [变更日志](../CHANGELOG.md)。

## 已移植的 challenge 协议与结果判定

参考上游 `7bdf82255532dd1317aadde594244b743d408763`（challenge 请求、等待 2.5 秒、SHA-256 proof）和 `4d3e1e5baea76086c43d7c02c2401b5639d2d715`（`repeated`/`checkedIn` 判定、本次积分解析）。设备恢复的第二次请求重新获取 challenge，并保持同一个 Cookie 和对应平台 UA。

本 fork 在 challenge 失败时停止该域名的签到，保留脱敏失败原因。明确的业务拒绝优先于矛盾的重复签到标记；`repeated=true` 与 `code=0` 的响应按重复签到处理，防止触发兑换。nonce 和 proof 仅保留在请求内存中，并参与错误文本脱敏。

此次移植包含上游已合并的 Python 协议逻辑。服务端要求 Turnstile 时仍需在官网完成验证，脚本保留非零退出码并提示手动签到。
