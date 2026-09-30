"""Account rescue from the server shell (run in the backend container, working dir /app):

    python -m app.modules.auth.cli list
    python -m app.modules.auth.cli reset-password <username>     # prints a new random password
    python -m app.modules.auth.cli make-admin <username>

A password reset signs the user out everywhere.
"""

import asyncio
import secrets
import sys

from app.core import registry
from app.core.auth import ADMIN
from app.db import init_db
from app.modules.auth import store


async def _main(args: list[str]) -> int:
    await init_db(registry.discover())
    if args[:1] == ["list"]:
        for u in await store.list_users():
            state = " (zablokovaný)" if u["disabled"] else ""
            print(f"{u['username']:<30} {u['role']}{state}")
        return 0
    if len(args) == 2 and args[0] in ("reset-password", "make-admin"):
        row = await store.get_row(username=args[1])
        if not row:
            print(f"Uživatel '{args[1]}' neexistuje", file=sys.stderr)
            return 1
        if args[0] == "reset-password":
            password = secrets.token_urlsafe(12)
            await store.update_user(row["id"], password=password, disabled=False)
            print(f"Nové heslo pro {row['username']}: {password}")
        else:
            await store.update_user(row["id"], role=ADMIN, disabled=False)
            print(f"{row['username']} je teď správce")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
