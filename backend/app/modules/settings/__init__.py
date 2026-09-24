"""App settings API, setup status, folder browser, language list."""

from app.clients.groq_scorer import DEFAULT_GROQ_MODEL, RETIRED_GROQ_MODELS
from app.core.module import Module
from app.modules.settings.router import router

_retired = ", ".join(f"'{m}'" for m in RETIRED_GROQ_MODELS)
# v1: Groq retired every model Lumina offered — move stored choices to the current default.
MIGRATE_RETIRED_GROQ = (
    f"UPDATE settings SET value = '{DEFAULT_GROQ_MODEL}' "
    f"WHERE key = 'groq_model' AND value IN ({_retired});"
)

module = Module(
    name="settings", title="Nastavení", order=1, required=True, routers=[router],
    migrations=[MIGRATE_RETIRED_GROQ],
)
