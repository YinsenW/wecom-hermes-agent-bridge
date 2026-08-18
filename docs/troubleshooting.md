# 故障排查

先保持 `BRIDGE_DRY_RUN=true`。不要通过关闭签名验证、开放 8080/8642、清空数据库或把 Secret 打到日志来定位问题。

| 现象 | 首要检查 | 常见原因 | 安全处理 |
| --- | --- | --- | --- |
| 域名没有解析 | `dig +short A DOMAIN @1.1.1.1` | A 记录错误、尚未传播、AAAA 配错 | 修正 DNS 并等待多地结果一致 |
| Certbot 失败 | `curl http://DOMAIN/.well-known/acme-challenge/test` | 80 未开放、DNS 未生效、WebRoot 错误 | 修复网络后再申请，避免反复触发限额 |
| HTTPS 证书不匹配 | `openssl s_client -connect DOMAIN:443 -servername DOMAIN` | SNI 分流错误、证书路径错误 | 检查 Nginx/HAProxy 后端，不使用 `-k` 作为正式修复 |
| 企业微信可信域名校验失败 | `curl https://DOMAIN/WW_verify_*.txt` | 文件名/内容被改、404、CDN 缓存、未备案 | 保证 200 且逐字一致；按后台提示完成主体/备案要求 |
| 回调配置保存失败 | Bridge/Nginx 日志、回调 URL | Token/AESKey/CorpID 不一致、查询参数丢失 | 精确比对配置，禁止关闭验签 |
| `/callbacks/wecom/kf` 返回 404 | `nginx -T` | server_name 或 location 未加载 | 检查启用站点、域名模板和 Nginx reload |
| 返回 502 | `curl 127.0.0.1:8080/health` | Bridge 未启动、代理目标错误 | 修复 systemd 服务和回环地址 |
| Hermes 健康但 Bridge 不回复 | Bridge `/health`、客服账号列表 | Secret/可信 IP/应用授权/open_kfid 错误 | 用 `list-kf-accounts.py` 验证应用可见账号 |
| Hermes 返回 401 | Bridge 和 Hermes 环境文件 | `HERMES_API_KEY` 与 `API_SERVER_KEY` 不一致 | 从受保护文件重新同步并重启，勿打印值 |
| Hermes 无模型结果 | `hermes doctor`、`hermes chat` | 模型提供商未配置、额度或 Key 问题 | 在 Hermes 层修复后再测试 Bridge |
| 消息积压 | `/health` 的 pending 数量、journal | 企业微信 API、Hermes 或数据库错误 | 先切演练/人工，保存证据后修复根因 |
| 重复回复 | 数据库和多实例状态 | 多个 Bridge 写同一账号、数据库被删、发送重试 | 只运行一个写实例，保留幂等数据库 |
| 人工客服被机器人抢答 | 客服状态、人工锁 | 状态授权错误或自定义改动绕过检查 | 立即停自动回复并验证状态转换接口 |
| 443 切换后原服务中断 | HAProxy 后端检查 | 原服务没有正确迁到回环端口/SNI 不兼容 | 执行共享 443 文档的预先准备回滚 |

## 最小诊断包

在不含秘密和客户内容的前提下收集：

```bash
date -Is
uname -a
sudo systemctl status wecom-hermes-agent-bridge --no-pager
sudo "$(command -v hermes)" gateway status --system
curl -fsS http://127.0.0.1:8080/health | jq
curl -fsS http://127.0.0.1:8642/health
sudo nginx -t
sudo ss -ltnp
```

分享日志前删除 Authorization、access_token、Secret、Token、AESKey、客户 ID、消息内容、内部路径和知识库片段。错误仍不明确时，只提供错误类型、HTTP 状态、企业微信 `errcode` 和发生时间。
