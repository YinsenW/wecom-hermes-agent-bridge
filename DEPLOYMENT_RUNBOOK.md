# Agent 执行手册：从空服务器到企业微信客服自动回复

本手册是本仓库的唯一标准上线流程。目标是让一个 Agent 在获得必要账号授权后，按阶段执行、逐步验收，并在外部条件或高风险操作前停下来找用户确认。

参考架构：

```text
微信客户
  → 企业微信「微信客服」
  → HTTPS https://wecom-kf.example.com/callbacks/wecom/kf
  → Nginx（或 HAProxy SNI → Nginx）
  → Bridge 127.0.0.1:8080
  → Hermes API 127.0.0.1:8642
  → 模型提供商
```

官方参考：

- [Hermes Agent 安装与文档](https://hermes-agent.nousresearch.com/docs/)
- [Hermes API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- [企业微信开发文档](https://developer.work.weixin.qq.com/document/path/94638)
- [企业微信开发教程](https://developer.work.weixin.qq.com/tutorial/%E5%85%A8%E9%83%A8)

## 阶段 0：收集输入，不执行修改

Agent 先创建一份不包含秘密值的实施记录，至少确认：

| 变量 | 示例 | 要求 |
| --- | --- | --- |
| `SERVER_IP` | `203.0.113.10` | 固定公网 IPv4；不是内网地址 |
| `DEPLOY_DOMAIN` | `wecom-kf.example.com` | 用户能修改 DNS；只填域名，不带协议/路径 |
| `SSH_USER` | `deploy` | 有 sudo 权限，优先不用 root 长期运行服务 |
| `SSH_PORT` | `22` | 已限制来源或使用密钥登录 |
| `PORT_443_MODE` | `exclusive` / `shared` | 443 是否已被其他服务占用 |
| `WECOM_CORP_ID` | 不记录真实值 | 企业 ID |
| `WECOM_APP_SECRET` | 不记录真实值 | 自建应用 Secret，必须走安全传输 |
| `OPEN_KF_ID` | 不记录真实值 | 目标客服账号的 `open_kfid` |
| `MODEL_PROVIDER` | `Nous Portal` 等 | Hermes 已支持的模型提供商 |
| `KNOWLEDGE_FILE` | 可选 | 审核后的对外知识库，不得直接使用内部原始文档 |

在执行会话中只导出非秘密变量，后续命令会使用它们：

```bash
export SERVER_IP='203.0.113.10'
export DEPLOY_DOMAIN='wecom-kf.example.com'
```

必须替换示例值。不要把 Secret、Token、AESKey 或模型 Key 导出到会被录屏/集中采集的终端。

必须由用户完成或授权的外部条件：

- 有权管理企业微信、自建应用和微信客服；
- 有权修改域名 DNS；
- 域名满足企业微信后台当前显示的主体/备案要求；
- 有一台可 SSH 的 Debian/Ubuntu 服务器；
- 有可用的 Hermes 模型提供商账号或 API Key；
- 同意服务器安装软件和创建服务账号；
- 若 80/443 已被占用，同意短暂停机、改端口和自动回滚。

**检查点 0：** 所有非秘密变量明确；秘密值有安全传输渠道；任何备案、付款、管理员审核或登录问题都已解决。否则暂停。

## 阶段 1：服务器只读预检

登录服务器后先做只读检查：

```bash
uname -a
cat /etc/os-release
df -h /
free -h
sudo systemctl is-system-running
sudo ss -ltnp
```

推荐至少 2 核、4 GB 内存和 20 GB 可用磁盘；低配机器也可能运行，但要降低并发并监控内存。确认系统时间与时区：

```bash
timedatectl status
```

安装基础工具：

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates openssl nginx certbot python3 python3-venv ripgrep dnsutils jq
```

克隆仓库并运行预检：

```bash
sudo install -d -o "$USER" -g "$USER" /opt/wecom-hermes-agent-bridge
git clone https://github.com/YinsenW/wecom-hermes-agent-bridge.git /opt/wecom-hermes-agent-bridge
cd /opt/wecom-hermes-agent-bridge
./scripts/preflight.sh "$DEPLOY_DOMAIN" "$SERVER_IP"
```

如果脚本报告 80 或 443 已有监听者：

- 80/443 都空闲：选择 `exclusive`；
- 443 被现有 TLS 服务占用：阅读 [共享 443 指南](docs/shared-port-443.md)，先做备份和回滚演练；
- 端口占用者不明：暂停，禁止直接停止进程。

**检查点 1：** 系统受支持、磁盘足够、时间正确、服务器可出站访问 HTTPS、端口模式已确认。

## 阶段 2：DNS 与网络

在 DNS 服务商中新增：

```text
类型：A
主机记录：wecom-kf（按实际子域名）
值：SERVER_IP
TTL：300 或服务商允许的较小值
```

没有可用 IPv6 时不要添加 AAAA。首次联调建议关闭 CDN/WAF 代理，使用纯 DNS 解析，避免代理修改查询参数、请求体或缓存回调。

服务器防火墙与云安全组：

- 入站 TCP 443：对公网开放；
- 入站 TCP 80：用于证书签发/续期，可对公网开放；
- 入站 SSH：只允许管理员来源 IP；
- 出站 TCP 443：允许访问企业微信、模型提供商、GitHub 和证书服务；
- 不开放 8080、8642。

DNS 验证：

```bash
dig +short A "$DEPLOY_DOMAIN" @1.1.1.1
dig +short A "$DEPLOY_DOMAIN" @8.8.8.8
```

两处结果都应包含 `SERVER_IP`。DNS 尚未传播时等待，不要提前申请证书反复触发限额。

**检查点 2：** 公共 DNS 的 A 记录一致；云防火墙规则正确；8080/8642 不可从公网访问。

## 阶段 3：安装并配置 Hermes Agent

以日常部署用户运行官方安装器。执行远程安装脚本前，Agent 必须展示来源并取得用户同意：

```bash
curl -fsSLo /tmp/hermes-install.sh https://hermes-agent.nousresearch.com/install.sh
less /tmp/hermes-install.sh
bash /tmp/hermes-install.sh
source ~/.bashrc
command -v hermes
hermes --version
```

执行官方设置向导：

```bash
hermes setup
```

若使用 Nous Portal，可按官方当前文档选择：

```bash
hermes setup --portal
```

这一步可能打开 OAuth、要求模型提供商 Key、付款或浏览器登录。Agent 必须暂停让用户完成，不得把 Key 写入仓库或实施记录。

验证 Hermes 基础能力：

```bash
hermes doctor
hermes model
hermes chat -q "只回复 HERMES_OK"
```

生成本项目需要的随机值，并写入仅当前用户可读的临时文件：

```bash
cd /opt/wecom-hermes-agent-bridge
python3 scripts/generate-secrets.py --output /tmp/wecom-hermes-generated.env
chmod 600 /tmp/wecom-hermes-generated.env
```

把其中 `HERMES_API_KEY` 安全写入 `~/.hermes/.env`，并设置：

```dotenv
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
API_SERVER_KEY=<生成的 HERMES_API_KEY>
```

不要设置 CORS；Bridge 是服务器到服务器调用。安装 Hermes 官方系统服务：

```bash
HERMES_BIN="$(command -v hermes)"
sudo "$HERMES_BIN" gateway install --system
sudo "$HERMES_BIN" gateway start --system
sudo "$HERMES_BIN" gateway status --system
```

`sudo` 会让 Hermes 官方安装器创建开机启动的 systemd 服务，并按原部署用户运行。若官方命令因版本变化而提示不同语法，以 `hermes gateway --help` 和官方 API Server 文档为准，记录差异后再继续。

验证：

```bash
curl -fsS http://127.0.0.1:8642/health
```

预期：`{"status":"ok"}`。再用安全读取到内存的 API Key 调用一次 `/v1/capabilities` 或 `/v1/chat/completions`；不要把 Authorization 头写入日志。

确认未公开监听：

```bash
sudo ss -ltnp | rg ':8642'
```

监听地址必须是 `127.0.0.1:8642` 或 `::1:8642`。

**检查点 3：** Hermes 能完成一次模型问答；Gateway 开机自启；健康检查成功；8642 仅回环监听。

## 阶段 4：安装 Bridge，保持演练模式

```bash
cd /opt/wecom-hermes-agent-bridge
./scripts/bootstrap.sh
sudo install -d -m 0750 /etc/wecom-hermes-agent-bridge
sudo install -m 0640 .env.example /etc/wecom-hermes-agent-bridge/bridge.env
sudoedit /etc/wecom-hermes-agent-bridge/bridge.env
```

填写真实值，但必须保持：

```dotenv
BRIDGE_ENV=production
BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=8080
BRIDGE_DB_PATH=/var/lib/wecom-hermes-agent-bridge/bridge.db
BRIDGE_DRY_RUN=true

WECOM_CORP_ID=<企业 ID>
WECOM_APP_SECRET=<自建应用 Secret>
WECOM_CALLBACK_TOKEN=<生成值或企业微信后台值>
WECOM_CALLBACK_AES_KEY=<生成值或企业微信后台值>
WECOM_ALLOWED_KF_IDS=<目标 open_kfid；未知时只可在演练阶段暂时留空>

HERMES_API_BASE=http://127.0.0.1:8642
HERMES_API_KEY=<与 Hermes API_SERVER_KEY 完全相同>
```

CorpID 和 Secret 必须是真实值。若暂时未知 `open_kfid`，演练阶段可以把 `WECOM_ALLOWED_KF_IDS` 留空；这代表不限制客服账号，所以必须在阶段 7 列出账号后立即写入目标 `open_kfid`，并且在此之前不能保存公网回调或关闭演练模式。不要把示例占位值当作真实配置。

安装 Bridge 服务：

```bash
sudo ./scripts/install-systemd.sh
sudo systemctl status wecom-hermes-agent-bridge --no-pager
curl -fsS http://127.0.0.1:8080/health | jq
```

健康结果至少应显示：

```json
{
  "status": "ok",
  "dry_run": true,
  "wecom_callback_configured": true,
  "wecom_api_configured": true,
  "hermes_configured": true,
  "sync_worker_running": true,
  "message_processor_running": true
}
```

确认 8080 只监听回环地址。

**检查点 4：** Bridge 启动成功、全部配置项为 true、演练模式为 true、8080 不公开。

## 阶段 5：域名、Nginx 与 HTTPS

### 5A. 443 未被占用

准备 WebRoot：

```bash
sudo install -d -o root -g www-data -m 0750 /var/www/wecom-domain-verification
sudo install -d -o root -g www-data -m 0755 /var/www/certbot
python3 scripts/render-domain-config.py \
  --template deploy/nginx/wecom-http-bootstrap.conf.example \
  --domain "$DEPLOY_DOMAIN" \
  --output /tmp/wecom-http-bootstrap.conf
sudo install -m 0644 /tmp/wecom-http-bootstrap.conf /etc/nginx/sites-available/wecom-hermes-agent-bridge.conf
sudo ln -sfn /etc/nginx/sites-available/wecom-hermes-agent-bridge.conf /etc/nginx/sites-enabled/wecom-hermes-agent-bridge.conf
sudo nginx -t
sudo systemctl reload nginx
```

申请证书：

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d "$DEPLOY_DOMAIN"
```

渲染最终配置：

```bash
python3 scripts/render-domain-config.py \
  --template deploy/nginx/wecom-hermes-agent-bridge.conf.example \
  --domain "$DEPLOY_DOMAIN" \
  --output /tmp/wecom-hermes-agent-bridge.conf
sudo install -m 0644 /tmp/wecom-hermes-agent-bridge.conf /etc/nginx/sites-available/wecom-hermes-agent-bridge.conf
sudo nginx -t
sudo systemctl reload nginx
```

### 5B. 443 已被其他 TLS 服务占用

不要直接停止现有服务。严格执行 [共享 443 指南](docs/shared-port-443.md)：备份现有配置、把原服务迁到回环后端端口、让 HAProxy 按 SNI 把 `DEPLOY_DOMAIN` 分到 Nginx 8443，其余域名进入原服务，并准备自动回滚。

### 5C. 验证 HTTPS 路由

```bash
./scripts/verify-deployment.sh "$DEPLOY_DOMAIN" "$SERVER_IP"
```

预期：DNS、TLS、Hermes、Bridge 均通过；不带企业微信签名访问回调时得到 422，证明路由存在但请求未通过业务校验。

**检查点 5：** 证书域名正确且未过期；443 可达；回调路径到达 Bridge；公网 `/health` 返回 404；证书自动续期测试成功。

证书续期测试：

```bash
sudo certbot renew --dry-run
```

## 阶段 6：企业微信域名归属验证

域名归属验证不等同于 ICP 备案。若企业微信后台提示“备案主体需与当前企业主体相同或有关联关系”，必须先满足后台要求；脚本不能绕过。

后台参考路径：

1. 企业微信管理后台 → 应用管理 → 选择用于客服 API 的自建应用。
2. 开发者接口 → 网页授权及 JS-SDK → 设置可信域名。
3. 填写 `DEPLOY_DOMAIN`，不带 `https://`，不带路径。
4. 点击“申请校验域名”，下载 `WW_verify_*.txt`。

Agent 请求用户把该文件安全上传到服务器临时目录，然后执行：

```bash
sudo ./scripts/install-domain-verification.sh /tmp/WW_verify_XXXXXXXX.txt
curl -fsS "https://$DEPLOY_DOMAIN/WW_verify_XXXXXXXX.txt"
```

浏览器/`curl` 返回内容必须与原文件逐字一致，HTTP 状态 200，不能跳登录页或被 CDN 改写。然后回到企业微信后台点击确认/完成校验。

建议保留验证文件，直到该可信域名不再使用。文件不包含业务秘密，但不要任意改名或改内容。

**检查点 6：** 企业微信页面明确显示可信域名设置成功；验证 URL 为 200；如有备案提示也已满足。

## 阶段 7：企业微信应用与客服账号授权

参考后台路径：

1. 应用管理 → 微信客服，确认功能已启用。
2. 创建或选择客服账号。
3. 微信客服应用详情 → API → “可调用接口的应用” → 设置。
4. 勾选用于本项目的自建应用。
5. 点击“前往配置”，把该自建应用关联到目标客服账号。
6. 回到自建应用 → 企业可信 IP，填写服务器**固定出口公网 IPv4**。

服务器出口 IP 可只读验证：

```bash
curl -4fsS https://api.ipify.org
```

若服务器经过 NAT，以此结果为准，不要填内网 IP。将结果与 `SERVER_IP` 比较；如果不同，记录“入站 IP”和“出口 IP”，企业可信 IP 填出口 IP。

先把 CorpID 和自建应用 Secret 写入受保护的 Bridge 环境文件，然后用官方 `kf/account/list` 接口列出可管理账号：

```bash
sudo /opt/wecom-hermes-agent-bridge/.venv/bin/python \
  /opt/wecom-hermes-agent-bridge/scripts/list-kf-accounts.py \
  --env-file /etc/wecom-hermes-agent-bridge/bridge.env
```

该命令只输出客服名称和 `open_kfid`，不会输出 Secret 或 access_token。若列表为空，回到后台检查自建应用是否已被选为“可调用接口的应用”并关联了客服账号。

获得并安全写入：

- CorpID → `WECOM_CORP_ID`
- 自建应用 Secret → `WECOM_APP_SECRET`
- 客服账号 `open_kfid` → `WECOM_ALLOWED_KF_IDS`

修改 `/etc/wecom-hermes-agent-bridge/bridge.env` 后重启：

```bash
sudo systemctl restart wecom-hermes-agent-bridge
curl -fsS http://127.0.0.1:8080/health | jq
```

**检查点 7：** 自建应用可调用目标客服账号；可信 IP 是真实出口 IP；Bridge 三项配置状态都为 true。

## 阶段 8：配置企业微信回调

后台参考路径：自建应用 → API 接收消息/接收消息服务器配置。

填写：

```text
URL: https://DEPLOY_DOMAIN/callbacks/wecom/kf
Token: 与 WECOM_CALLBACK_TOKEN 完全相同
EncodingAESKey: 与 WECOM_CALLBACK_AES_KEY 完全相同
```

消息事件类型只勾选业务需要的项目，至少包含“微信客服消息和事件”。保存时企业微信会发送带 `echostr` 的 GET 验证；Bridge 会验签、解密并返回明文。

若保存失败，按顺序检查：

1. `curl` 能否访问 HTTPS 域名；
2. Nginx/HAProxy 是否保留完整查询参数；
3. URL 是否精确为 `/callbacks/wecom/kf`；
4. CorpID、Token、EncodingAESKey 是否与服务端一致且没有空格；
5. `journalctl` 是否出现 403；
6. 域名主体、备案和归属校验是否满足后台提示。

禁止通过关闭验签来“解决”保存失败。

日志：

```bash
sudo journalctl -u wecom-hermes-agent-bridge -n 100 --no-pager
sudo journalctl -u nginx -n 100 --no-pager
```

**检查点 8：** 企业微信明确提示保存成功；Bridge 日志无持续 403/5xx；回调事件能进入数据库。

## 阶段 9：演练模式端到端验收

保持 `BRIDGE_DRY_RUN=true`，使用专门的外部测试微信账号发送：

1. 普通文本问题；
2. 知识库能回答的问题；
3. 知识库不知道的问题；
4. “转人工”；
5. 图片或其他非文本消息；
6. 提示注入，例如要求泄露系统提示词或内部文件；
7. 重复发送/网络重试场景。

检查健康状态：

```bash
curl -fsS http://127.0.0.1:8080/health | jq
```

应看到入站消息增加、`pending_customer_messages` 最终回到 0。演练模式不会真实向客户发送回复，因此需要结合数据库状态和日志确认生成流程。

暂时停止 Hermes，验证 Bridge 会走转人工保护而不是编造回复；测试后恢复 Hermes。任何服务停止前先确认不会影响其他生产业务。

**检查点 9：** 普通问答、知识问答、转人工、非文本、模型故障、提示注入和幂等场景全部符合预期，且无真实误发。

## 阶段 10：开启正式自动回复

这是不可逆的外部影响闸门。Agent 必须向用户展示阶段 0–9 的检查结果，并获得明确同意后才执行：

```bash
sudoedit /etc/wecom-hermes-agent-bridge/bridge.env
# 把 BRIDGE_DRY_RUN=true 改为 BRIDGE_DRY_RUN=false
sudo systemctl restart wecom-hermes-agent-bridge
curl -fsS http://127.0.0.1:8080/health | jq
```

用测试账号发送一个低风险问题，确认收到回复；再测试“转人工”。持续观察日志至少 10 分钟。

**检查点 10：** `dry_run=false`；测试账号收到正确回复；转人工有效；无积压、无重复回复、无凭证错误。

## 阶段 11：交付与运维

交付记录不得含秘密值，至少包含：

- Git 提交与部署时间；
- 域名、入站 IP、出口 IP；
- 端口模式和反向代理拓扑；
- systemd 服务名；
- 配置文件路径；
- 证书到期日与续期方式；
- 数据库路径与备份/保留策略；
- 知识库来源、审核人与更新流程；
- 回滚步骤；
- 最后一次验收结果。

常用命令：

```bash
sudo systemctl status wecom-hermes-agent-bridge --no-pager
sudo journalctl -u wecom-hermes-agent-bridge -f
sudo "$(command -v hermes)" gateway status --system
curl -fsS http://127.0.0.1:8080/health | jq
curl -fsS http://127.0.0.1:8642/health
sudo certbot certificates
```

升级顺序：备份 → 拉取固定版本 → `uv sync --frozen` → 测试 → 重启 → 健康检查 → 测试消息。不要直接在生产机跟随未知的 `main` 最新提交。

## 紧急回滚

出现误答、重复回复、模型异常或数据风险时，优先恢复人工服务：

1. 把 `BRIDGE_DRY_RUN` 改回 `true` 并重启；或停止 Bridge；
2. 确认企业微信人工接待仍可用；
3. 保存日志和数据库副本用于分析，不要公开；
4. 若泄露凭证，立即轮换对应 Secret/Token/AESKey/API Key；
5. 若是 443 分流故障，执行 [共享 443 指南](docs/shared-port-443.md) 中已准备的回滚命令。

不要通过删除数据库解决重复消息问题；这可能丢失去重和会话状态。
