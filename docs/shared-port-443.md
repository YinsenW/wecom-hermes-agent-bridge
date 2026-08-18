# 443 端口已被占用时的 SNI 分流

只有在现有服务已经占用公网 443 时使用本方案。它会把 HAProxy 设为唯一的 443 入口，根据 TLS ClientHello 中的 SNI 把企业微信域名转给 Nginx，其余流量交还原服务。

这是高风险变更。Agent 必须先取得用户明确同意，保持第二个 SSH 会话，备份所有配置，并准备可在一分钟内执行的回滚命令。

## 前提

- 原服务可以改为监听 `127.0.0.1:9443` 或其他未占用端口；
- 客户端会发送标准 TLS SNI；
- 企业微信域名是独立域名；
- Nginx 可以监听 `127.0.0.1:8443`；
- TCP 443 没有内核级透明代理或云负载均衡冲突。

不满足任一项时，不要套用示例；改用独立 IP、独立服务器或云负载均衡。

## 1. 盘点和备份

```bash
sudo ss -ltnp '( sport = :443 )'
sudo systemctl status haproxy nginx --no-pager
sudo install -d -m 0700 /root/wecom-443-backup
```

将现有 TLS 服务及代理配置复制到 `/root/wecom-443-backup/`，记录原服务启动、停止和恢复命令。先验证备份可读，禁止只做口头记录。

## 2. 准备 Nginx 8443 后端

先通过 80 端口的 WebRoot 方式取得证书，再渲染模板：

```bash
python3 scripts/render-domain-config.py \
  --template deploy/nginx/wecom-hermes-agent-bridge-behind-haproxy.conf.example \
  --domain "$DEPLOY_DOMAIN" \
  --output /tmp/wecom-behind-haproxy.conf
sudo install -m 0644 /tmp/wecom-behind-haproxy.conf /etc/nginx/sites-available/wecom-hermes-agent-bridge.conf
sudo nginx -t
sudo systemctl reload nginx
curl -ksS --resolve "$DEPLOY_DOMAIN:8443:127.0.0.1" "https://$DEPLOY_DOMAIN:8443/callbacks/wecom/kf" -o /dev/null -w '%{http_code}\n'
```

最后一条预期返回 422。`-k` 仅用于本机端口测试；正式公网验证不得跳过证书校验。

## 3. 把原服务迁到回环端口

按照原服务文档，把监听从 `0.0.0.0:443` 改为 `127.0.0.1:9443`。先用其原有客户端或健康检查直接验证 9443。

如果原服务不能安全迁移，立即停止本方案，不要让 HAProxy 转发到一个未经验证的后端。

## 4. 配置 HAProxy

安装并基于 [示例配置](../deploy/haproxy/haproxy-sni-router.cfg.example) 合并现有 HAProxy 配置：

```bash
sudo apt-get install -y haproxy
python3 scripts/render-domain-config.py \
  --template deploy/haproxy/haproxy-sni-router.cfg.example \
  --domain "$DEPLOY_DOMAIN" \
  --output /tmp/haproxy-wecom.cfg
sudo haproxy -c -f /tmp/haproxy-wecom.cfg
```

不要盲目覆盖已有 `/etc/haproxy/haproxy.cfg`。若服务器原本没有 HAProxy，确认示例中的原服务后端端口正确后再安装。切换顺序应尽量缩短中断：

1. Nginx 8443 已健康；
2. 原服务 9443 已健康；
3. HAProxy 配置校验通过；
4. 原服务释放 443；
5. HAProxy 立即绑定 443；
6. 同时测试企业微信域名和原服务。

## 5. 验收

```bash
openssl s_client -connect "$DEPLOY_DOMAIN:443" -servername "$DEPLOY_DOMAIN" </dev/null
./scripts/verify-deployment.sh "$DEPLOY_DOMAIN" "$SERVER_IP"
sudo ss -ltnp '( sport = :443 or sport = :8443 or sport = :9443 )'
```

预期只有 HAProxy 公开监听 443；Nginx 8443 与原服务 9443 只监听回环地址。

## 回滚

任何公网测试失败时立即：

1. 停止 HAProxy；
2. 恢复原服务的 443 监听配置；
3. 启动/重启原服务；
4. 用原有客户端验证恢复；
5. 保留失败的 HAProxy/Nginx 日志再排查。

回滚命令必须在切换前根据实际服务写好并经用户确认。仓库无法替未知服务生成安全的通用回滚命令。
