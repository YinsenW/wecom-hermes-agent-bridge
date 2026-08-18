#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


PLACEHOLDER = "wecom-kf.example.com"
DOMAIN_RE = re.compile(
    r"(?=^.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render an example proxy configuration for a validated domain."
    )
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    domain = args.domain.strip().lower().rstrip(".")
    if not DOMAIN_RE.fullmatch(domain):
        raise SystemExit(f"Invalid DNS name: {args.domain!r}")
    source = args.template.read_text(encoding="utf-8")
    if PLACEHOLDER not in source:
        raise SystemExit(f"Template does not contain {PLACEHOLDER!r}")
    rendered = source.replace(PLACEHOLDER, domain)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=args.output.parent,
        prefix=f".{args.output.name}.",
        delete=False,
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    os.chmod(temporary, 0o644)
    temporary.replace(args.output)
    print(f"Rendered {args.output} for {domain}")


if __name__ == "__main__":
    main()
