# WeCom Hermes Agent Bridge

[English](#english-summary) · [部署指南](docs/deployment.md) · [企业微信配置](docs/wecom-setup.md) · [知识库](docs/knowledge-base.md)

把企业微信「微信客服」接到 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的开源桥接服务。它负责企业微信回调验签与解密、客服消息拉取、会话隔离、自动回复、失败转人工，以及轻量 Markdown 知识检索。

> 项目默认 `BRIDGE_DRY_RUN=true`，不会向真实客户发送消息。完成测试与业务审核后再显式关闭演练模式。

## 能做什么

- 验证并解密企业微信加密回调
- 通过 `sync_msg` 拉取消息，保存游标并按 `msgid` 去重
- 为每个客户会话创建隔离、不可猜测的 Hermes Session
- 自动回复文本消息；非文本消息、模型失败或客户主动要求时转人工
- 识别人工接待状态，避免机器人抢答
- 从单个 Markdown 文件按问题检索相关章节并注入只读上下文
- SQLite 持久化、幂等发送、健康检查和安全默认值

## 工作流程

```text
微信客户
   │
   ▼
企业微信客服 ──加密回调──▶ Bridge ──sync_msg──▶ 企业微信 API
                              │
                              ├── SQLite：游标、去重、会话、发送状态
                              ├── Markdown：按需检索知识片段
                              ▼
                         Hermes Sessions API
                              │
                              ▼
                    send_msg / 转人工状态切换
```

更完整的组件边界与状态流见 [架构说明](docs/architecture.md)。

## 快速启动

要求：Python 3.11+、[uv](https://docs.astral.sh/uv/) 和一个可访问的 Hermes Sessions API。

```bash
cp .env.example .env
uv sync --frozen --dev
uv run uvicorn wecom_hermes_bridge.main:app \
  --env-file .env --host 127.0.0.1 --port 8080
```

检查服务：

```bash
curl http://127.0.0.1:8080/health
```

第一次联调建议保持：

```dotenv
BRIDGE_DRY_RUN=true
```

然后依次完成：

1. 按 [.env.example](.env.example) 填写企业微信与 Hermes 参数。
2. 为回调服务配置 HTTPS 反向代理。
3. 在企业微信后台设置回调地址、Token、EncodingAESKey，并只勾选「微信客服消息和事件」。
4. 发送测试消息，确认 `/health` 中待处理数量归零且没有异常。
5. 完成内容、转人工和失败场景测试后，把 `BRIDGE_DRY_RUN` 改为 `false`。

服务器部署见 [部署指南](docs/deployment.md)，后台逐项配置见 [企业微信配置](docs/wecom-setup.md)。

## 配置

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `WECOM_CORP_ID` | 是 | — | 企业 ID |
| `WECOM_APP_SECRET` | 是 | — | 被授权自建应用的 Secret |
| `WECOM_CALLBACK_TOKEN` | 是 | — | 回调 Token |
| `WECOM_CALLBACK_AES_KEY` | 是 | — | 43 位 EncodingAESKey |
| `WECOM_ALLOWED_KF_IDS` | 建议 | 空 | 允许接入的 `open_kfid`，逗号分隔 |
| `HERMES_API_BASE` | 是 | `http://127.0.0.1:8642` | Hermes Sessions API 地址 |
| `HERMES_API_KEY` | 是 | — | Hermes API Bearer Token |
| `BRIDGE_DB_PATH` | 否 | `data/bridge.db` | SQLite 文件路径 |
| `BRIDGE_DRY_RUN` | 否 | `true` | 演练模式，不向客户发送消息 |
| `KNOWLEDGE_BASE_PATH` | 否 | 空 | 单个 Markdown 知识库文件 |
| `KNOWLEDGE_TRIGGER_TERMS` | 否 | 空 | 只在命中关键词时检索，逗号分隔 |
| `KNOWLEDGE_TOP_K` | 否 | `4` | 最多注入的章节数 |
| `KNOWLEDGE_MAX_CHARS` | 否 | `6000` | 知识上下文字符上限 |

## 测试

```bash
uv sync --frozen --dev
uv run pytest
./scripts/check.sh
```

## 安全提示

- 不要提交 `.env`、数据库、SSH 私钥、真实回调凭证或内部知识文档。
- 生产环境只把桥接服务绑定到本机地址，通过 HTTPS 反向代理暴露回调路径。
- Hermes API 建议仅监听回环地址，并使用独立强随机 API Key。
- 限制 `WECOM_ALLOWED_KF_IDS`，并在正式启用前测试转人工与失败保护。
- 知识库文本属于不可信只读资料；当前实现包含提示注入隔离，但不能代替内容审核和权限分库。

详见 [SECURITY.md](SECURITY.md)。

## 项目状态

这是可运行的参考实现，不是腾讯或 Nous Research 官方项目。生产使用前请结合自己的合规、隐私、审计、监控和容灾要求评估。

## English summary

WeCom Hermes Agent Bridge connects WeCom Customer Service to the Hermes Sessions API. It provides encrypted callback handling, message synchronization and deduplication, isolated customer sessions, safe human handoff, SQLite persistence, and lightweight Markdown retrieval. Start in dry-run mode and review the [deployment guide](docs/deployment.md) before production use.

## License

[MIT](LICENSE)
