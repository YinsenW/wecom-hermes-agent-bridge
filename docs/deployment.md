# 服务器部署

需要从空服务器一直完成 DNS、Hermes、HTTPS、企业微信域名归属验证、后台授权和端到端验收时，请使用根目录的 [Agent 执行手册](../DEPLOYMENT_RUNBOOK.md)。本页仅保留熟悉本项目人员的简版操作。

下面以 Debian/Ubuntu、systemd 和 Nginx 为例。所有域名、路径、用户和密钥都是示例。

## 1. 安装代码

```bash
sudo mkdir -p /opt/wecom-hermes-agent-bridge
sudo chown "$USER":"$USER" /opt/wecom-hermes-agent-bridge
git clone https://github.com/YinsenW/wecom-hermes-agent-bridge.git /opt/wecom-hermes-agent-bridge
cd /opt/wecom-hermes-agent-bridge
./scripts/bootstrap.sh
```

Hermes Agent 应独立安装并启动 Sessions API。Bridge 默认连接 `http://127.0.0.1:8642`；不要把 Hermes API 直接暴露到公网。

## 2. 写入生产配置

```bash
sudo install -d -m 0750 /etc/wecom-hermes-agent-bridge
sudo install -m 0640 .env.example /etc/wecom-hermes-agent-bridge/bridge.env
sudoedit /etc/wecom-hermes-agent-bridge/bridge.env
```

至少替换所有 `replace_with_...` 值，并保持 `BRIDGE_DRY_RUN=true` 完成联调。生产数据库路径应改为 `/var/lib/wecom-hermes-agent-bridge/bridge.db`。

## 3. 安装 systemd 服务

先检查 [systemd 服务文件](../deploy/systemd/wecom-hermes-agent-bridge.service) 中的路径与用户，再执行：

```bash
sudo ./scripts/install-systemd.sh
sudo systemctl status wecom-hermes-agent-bridge
```

查看日志：

```bash
sudo journalctl -u wecom-hermes-agent-bridge -f
```

## 4. 配置 HTTPS

复制 [Nginx 示例](../deploy/nginx/wecom-hermes-agent-bridge.conf.example)，替换 `wecom-kf.example.com` 和证书路径。Nginx 只代理回调路径和可选健康检查；Bridge 仍监听 `127.0.0.1:8080`。

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 5. 上线检查

```bash
curl http://127.0.0.1:8080/health
```

上线前确认：

- 企业微信保存回调成功；
- 公网只暴露精确的回调路径，不公开 `/health`；
- 普通消息能完成但演练模式不真实发送；
- 转人工、非文本、Hermes 断连场景符合预期；
- 数据库目录仅服务账号可写；
- 环境文件仅 root 与服务账号可读；
- 已配置监控、日志保留、备份和凭证轮换流程。

最后把 `BRIDGE_DRY_RUN=false`，重启服务并用专用测试客服账号验证一次。

## 升级与回滚

升级前备份数据库和环境文件。拉取新版本后运行 `uv sync --frozen` 和测试，再重启服务。回滚时切回上一个已验证的 Git 标签并恢复对应数据库备份；不要在未知 schema 之间盲目切换。
