from __future__ import annotations

import xml.etree.ElementTree as ET


class XMLPayloadError(ValueError):
    """Raised when a callback XML payload is invalid."""


def parse_flat_xml(payload: str | bytes) -> dict[str, str]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise XMLPayloadError("Invalid XML payload") from exc
    return {child.tag: child.text or "" for child in root}
