#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi
if [[ "$#" -ne 1 ]]; then
  echo "Usage: sudo $0 /path/to/WW_verify_XXXXXXXX.txt" >&2
  exit 2
fi

source_file="$1"
file_name="$(basename "$source_file")"
if [[ ! "$file_name" =~ ^WW_verify_[A-Za-z0-9]+\.txt$ ]]; then
  echo "Unexpected verification filename: $file_name" >&2
  exit 2
fi
if [[ ! -f "$source_file" || ! -s "$source_file" ]]; then
  echo "Verification file is missing or empty: $source_file" >&2
  exit 1
fi
file_size="$(wc -c < "$source_file")"
if (( file_size > 4096 )); then
  echo "Verification file is unexpectedly large: ${file_size} bytes" >&2
  exit 1
fi
if ! getent group www-data >/dev/null; then
  echo "Group www-data does not exist; install Nginx first." >&2
  exit 1
fi

target_dir="/var/www/wecom-domain-verification"
install -d -o root -g www-data -m 0750 "$target_dir"
install -o root -g www-data -m 0640 "$source_file" "$target_dir/$file_name"
echo "Installed: $target_dir/$file_name"
echo "Verify: https://YOUR_DOMAIN/$file_name"
