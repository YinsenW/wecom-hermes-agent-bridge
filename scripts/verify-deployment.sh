#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  echo "Usage: $0 DOMAIN EXPECTED_PUBLIC_IPV4 [WW_VERIFY_FILE]" >&2
  exit 2
fi

deploy_domain="${1%.}"
expected_ip="$2"
verification_file="${3:-}"

required_commands=(cmp curl dig jq mktemp rg)
for command_name in "${required_commands[@]}"; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing command: $command_name" >&2
    exit 1
  fi
done

echo "[1/7] DNS"
dns_answers="$(dig +short A "$deploy_domain" @1.1.1.1 | sort -u)"
echo "$dns_answers"
rg -x --fixed-strings "$expected_ip" <<<"$dns_answers" >/dev/null

echo "[2/7] TLS certificate and hostname"
curl -sS --max-time 15 "https://$deploy_domain/this-path-must-not-exist" -o /dev/null -w 'HTTP %{http_code}\n' | rg 'HTTP 404'

echo "[3/7] Public callback routing"
callback_status="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "https://$deploy_domain/callbacks/wecom/kf")"
if [[ "$callback_status" != "422" ]]; then
  echo "Expected unsigned callback probe to return 422, got $callback_status" >&2
  exit 1
fi
echo "Unsigned callback probe returned expected HTTP 422."

echo "[4/7] Bridge local health"
curl -fsS http://127.0.0.1:8080/health | jq -e '.status == "ok"' >/dev/null

echo "[5/7] Hermes local health"
curl -fsS http://127.0.0.1:8642/health | jq -e '.status == "ok"' >/dev/null

echo "[6/7] Public health endpoint is not exposed"
health_status="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "https://$deploy_domain/health")"
if [[ "$health_status" != "404" ]]; then
  echo "Expected public /health to return 404, got $health_status" >&2
  exit 1
fi

echo "[7/7] Optional WeCom domain-verification file"
if [[ -n "$verification_file" ]]; then
  file_name="$(basename "$verification_file")"
  temporary_file="$(mktemp)"
  trap 'rm -f "$temporary_file"' EXIT
  curl -fsS "https://$deploy_domain/$file_name" -o "$temporary_file"
  cmp "$verification_file" "$temporary_file"
  echo "Verification file matches byte-for-byte."
else
  echo "Skipped; pass the WW_verify file as the third argument after installation."
fi

echo "Deployment checks passed. WeCom administrator validation and dry-run message tests are still required."
