# Security Policy

## Reporting a vulnerability

Please open a private security advisory in this repository. Do not publish working exploits, credentials, customer data, callback payloads, or infrastructure details in a public issue.

## Deployment baseline

- Keep `BRIDGE_DRY_RUN=true` until end-to-end tests pass.
- Bind the bridge and Hermes API to loopback/private interfaces; expose only the HTTPS callback through a reverse proxy.
- Generate independent, high-entropy values for the WeCom callback Token, EncodingAESKey, application Secret, and Hermes API key.
- Store secrets in a root-readable environment file or a managed secret store, never in Git.
- Restrict `WECOM_ALLOWED_KF_IDS` to the intended customer-service accounts.
- Rotate credentials after accidental screenshots, logs, commits, or chat disclosure.
- Treat all customer messages and knowledge documents as untrusted input.

## Data handling

The bridge stores message payloads and conversation metadata in SQLite. Define a retention policy, filesystem permissions, encrypted backups, and deletion procedures appropriate for your jurisdiction and business.

The Markdown knowledge retriever is not an authorization system. Use separate processes or stores for data with different visibility, and review any content that may be sent to external customers.
