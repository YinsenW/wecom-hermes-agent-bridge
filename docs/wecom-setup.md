# 企业微信配置

部署 Agent 应先完成服务器、HTTPS 和演练模式验证，再自行使用可用的浏览器自动化配置企业微信。只有管理员登录、扫码、验证码或保存确认无法自动化时，才让人按 [人工网页操作清单](../HUMAN_WEB_CHECKLIST.md) 操作；人不需要运行服务器命令，也不应把 Secret、Token 或 EncodingAESKey 发回聊天。

企业微信后台界面会调整，以下名称以「应用管理 → 微信客服」为参考。

## 1. 准备自建应用

创建或选择一个自建应用，记录：

- 企业 ID（CorpID）
- 应用 Secret
- 需要管理的客服账号 `open_kfid`

在微信客服的「可调用接口的应用」中选择这个自建应用，并把目标客服账号分配给它。应用需要配置可信 IP；生产环境应填写服务器固定出口 IP。可用 [账号列表脚本](../scripts/list-kf-accounts.py) 调用官方 `kf/account/list` 接口确认应用实际能看到哪些 `open_kfid`。

## 2. 准备回调地址

假设你的公开域名是 `wecom-kf.example.com`，则回调地址为：

```text
https://wecom-kf.example.com/callbacks/wecom/kf
```

要求：HTTPS 证书有效；域名解析到反向代理；代理把该路径转发到 Bridge；GET 和 POST 均可访问；代理不修改查询参数或请求体。

## 3. 配置 API 接收消息

在自建应用的「接收消息」中填写：

- URL：上面的完整回调地址
- Token：自行生成的随机值，并同步写入 `WECOM_CALLBACK_TOKEN`
- EncodingAESKey：随机生成，并同步写入 `WECOM_CALLBACK_AES_KEY`

消息事件类型至少选择「微信客服消息和事件」。为了最小权限，除非业务确实需要，不要勾选无关事件。

保存时，企业微信会发起 GET 验证。Bridge 必须已启动且 CorpID、Token、EncodingAESKey 完全一致。

## 4. 配置可信域名与可信 IP

- 可信域名填写域名，不带协议和路径，例如 `wecom-kf.example.com`。
- 点击“申请校验域名”后，后台会下载 `WW_verify_*.txt`。保持文件名和内容不变，把它部署到网站根路径，确保 `https://域名/WW_verify_*.txt` 返回 200 和原始内容，再回后台确认。
- 域名归属校验不等于 ICP 备案。若后台显示备案主体与企业主体关系要求，必须先按提示完成，不能用反向代理或脚本绕过。
- 企业可信 IP 填服务器固定出口 IP，不填本地电脑的临时公网 IP。

仓库提供了验证文件安装命令：

```bash
sudo ./scripts/install-domain-verification.sh /tmp/WW_verify_XXXXXXXX.txt
./scripts/verify-deployment.sh wecom-kf.example.com 203.0.113.10 /tmp/WW_verify_XXXXXXXX.txt
```

## 5. 联调顺序

1. 保持 `BRIDGE_DRY_RUN=true` 启动服务。
2. 打开 `/health`，确认回调、企业微信 API 和 Hermes 均显示已配置。
3. 保存回调配置，确认企业微信提示验证成功。
4. 从测试微信账号发送文本消息。
5. 检查 `inbound_messages` 增加且 `pending_customer_messages` 回到 0。
6. 分别测试普通问答、人工客服关键词、图片消息和 Hermes 不可用场景。
7. 内容审核通过后，设置 `BRIDGE_DRY_RUN=false` 并重启。

## 常见问题

- **回调保存失败**：优先检查 DNS、证书、URL 路径、Token/AESKey 是否一致，以及 CorpID 是否正确。
- **能收到回调但不回复**：检查应用是否被授权管理该客服账号、可信 IP、Secret、`open_kfid` 白名单和 Hermes 健康状态。
- **重复回复**：不要删除运行中的数据库；确认部署只使用一个写实例，或自行改造为共享存储与分布式锁。
