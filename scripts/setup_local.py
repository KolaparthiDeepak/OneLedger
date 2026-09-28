"""Create a local .env with freshly generated secrets (never overwrites an existing .env)."""

from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
env = root / ".env"
if env.exists():
    print(".env already exists; leaving it unchanged.")
    sys.exit(0)


def key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


runtime_password = secrets.token_urlsafe(18)
text = (root / ".env.example").read_text()
replacements = {
    "SECRET_ENCRYPTION_KEY=": f"SECRET_ENCRYPTION_KEY={key()}",
    "TOKEN_HASH_KEY=": f"TOKEN_HASH_KEY={key()}",
    "SCHEDULER_SECRET=": f"SCHEDULER_SECRET={secrets.token_urlsafe(32)}",
    "BFF_SHARED_SECRET=": f"BFF_SHARED_SECRET={secrets.token_urlsafe(32)}",
    "oneledger_runtime:CHANGE_ME@": f"oneledger_runtime:{runtime_password}@",
}
lines = []
for line in text.splitlines():
    for old, new in replacements.items():
        if line.startswith(old) or old in line:
            line = line.replace(old, new, 1) if old in line else line
    lines.append(line)
env.write_text("\n".join(lines) + "\n")
env.chmod(0o600)
print(f"Wrote {env} (mode 600). Runtime DB password generated.")
