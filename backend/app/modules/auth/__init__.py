"""Sign-in and users (decisions/0006).

Accounts are local (username + password), roles admin / user; a user gets the permissions the
admin picks from the ones the modules declare. The first admin is created in the web UI with a
one-time code from the backend log. Forgotten password: ``python -m app.modules.auth.cli``.
"""

from app.core.module import Module
from app.modules.auth.router import prepare_setup, router

module = Module(
    name="auth",
    title="Přihlášení",
    order=1,
    required=True,
    requires_login=False,   # the endpoints guard themselves (login and status must be public)
    routers=[router],
    on_startup=[prepare_setup],
)
