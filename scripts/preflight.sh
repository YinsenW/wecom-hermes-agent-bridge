#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "Usage: $0 DOMAIN EXPECTED_PUBLIC_IPV4" >&2
  exit 2
fi

deploy_domain="${1%.}"
expected_ip="$2"

if [[ ! "$deploy_domain" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; then
  echo "Invalid domain: $deploy_domain" >&2
  exit 2
fi

python3 -c 'import ipaddress,sys; value=ipaddress.ip_address(sys.argv[1]); assert value.version == 4 and not value.is_private' "$expected_ip" || {
  echo "EXPECTED_PUBLIC_IPV4 is not a public IPv4 address: $expected_ip" >&2
  exit 2
}

required_commands=(curl git openssl python3 rg systemctl ss)
missing=()
for command_name in "${required_commands[@]}"; do
  command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
done
if (( ${#missing[@]} > 0 )); then
  echo "Missing commands: ${missing[*]}" >&2
  exit 1
fi

echo "OS: $(. /etc/os-release && echo "${PRETTY_NAME:-unknown}")"
echo "Kernel: $(uname -srmo)"
echo "Root filesystem:"
df -h /
echo "Memory:"
free -h || true
echo "Time:"
timedatectl show --property=Timezone --property=NTPSynchronized --property=LocalRTC || true

if command -v dig >/dev/null 2>&1; then
  dns_answers="$(dig +short A "$deploy_domain" @1.1.1.1 | sort -u)"
  echo "Public DNS A records for $deploy_domain:"
  echo "${dns_answers:-<none>}"
  if [[ -n "$dns_answers" ]] && ! rg -x --fixed-strings "$expected_ip" <<<"$dns_answers" >/dev/null; then
    echo "DNS does not contain expected IP $expected_ip" >&2
    exit 1
  fi
else
  echo "dig is not installed; DNS propagation check skipped."
fi

echo "Current listeners on TCP 80/443 (empty is expected for exclusive mode):"
ss -ltnp '( sport = :80 or sport = :443 )' || true

echo "Outbound HTTPS check:"
curl -fsS --max-time 10 https://qyapi.weixin.qq.com/cgi-bin/gettoken -o /dev/null
echo "WeCom API endpoint is reachable."

echo "Preflight complete. Resolve any DNS mismatch or unknown port owner before modifying services."
