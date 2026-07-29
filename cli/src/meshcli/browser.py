"""Best-effort browser launch (cli.md §4.2 golden path).

Never fatal: a headless box (SSH/CI) simply prints the URL + code and the
user opens it on another device — which is exactly what the device-code flow
exists for.
"""

from __future__ import annotations

import subprocess
import sys


def try_open(url: str) -> bool:
    """Attempt to open ``url`` in the default browser; True on apparent success."""
    platform = sys.platform
    try:
        if platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif platform.startswith("win"):
            subprocess.Popen(["cmd", "/c", "start", "", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
