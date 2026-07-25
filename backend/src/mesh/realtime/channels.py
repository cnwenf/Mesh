"""Channel name parsing and validation (README §6.7).

Channel names are strings like ``issue:<uuid>`` / ``execution:<uuid>:logs`` /
``workspace:<uuid>:issues``. The channel string is NEVER a tenant isolation
boundary (§6.2 rule 8) — ownership is validated against
``realtime_channels.workspace_id`` by the authorizer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# entity = lowercase letters/underscores; key = anything but whitespace/control chars.
_CHANNEL_RE = re.compile(r"^([a-z][a-z_]*):([^\s\x00-\x1f]+)$")
MAX_CHANNEL_LENGTH = 255


@dataclass(frozen=True)
class ChannelInfo:
    """A parsed channel name."""

    entity: str
    key: str
    raw: str


def parse_channel(name: str) -> ChannelInfo | None:
    """Parse a channel name; returns None when syntactically invalid."""
    if not isinstance(name, str) or len(name) > MAX_CHANNEL_LENGTH:
        return None
    match = _CHANNEL_RE.match(name)
    if match is None:
        return None
    return ChannelInfo(entity=match.group(1), key=match.group(2), raw=name)


def is_valid_channel(name: str) -> bool:
    """True when ``name`` is a syntactically valid channel."""
    return parse_channel(name) is not None
