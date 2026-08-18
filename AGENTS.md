# Agent instructions

When the user asks you to deploy this repository, start with [DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md) and execute it in order. Do not improvise a different architecture until the documented preflight has identified a concrete incompatibility.

Non-negotiable rules:

1. Never commit, print, paste into issues, or include in chat logs any real WeCom Secret, callback Token, EncodingAESKey, Hermes provider key, Hermes API key, SSH private key, customer data, or knowledge-base content.
2. Use placeholders in files under Git. Put runtime secrets only in the protected paths documented by the Runbook.
3. Begin with `BRIDGE_DRY_RUN=true`. Do not enable real customer replies until every dry-run acceptance test passes and the user explicitly approves activation.
4. Ask before changing an existing service that owns TCP 80 or 443. Back up its configuration and prepare the documented rollback before switching traffic.
5. Treat ICP filing, WeCom administrator approval, domain ownership verification, CAPTCHA, SMS, QR login, billing, and model-provider authorization as user gates. Pause and request the minimum necessary action; never claim these gates succeeded without evidence.
6. At the end of every phase, record the command result and compare it with the phase checkpoint. Do not continue after a failed checkpoint.
7. Do not expose Hermes port 8642 or Bridge port 8080 to the public Internet. Public ingress is HTTPS on the exact WeCom callback and domain-verification paths only.
8. If a secret appears in a screenshot, terminal transcript, Git history, or issue, stop and rotate it before production activation.

The reference deployment target is Debian/Ubuntu with systemd, a fixed public IPv4 address, a controlled domain, Nginx, and optional HAProxy SNI routing when another service already owns port 443.
