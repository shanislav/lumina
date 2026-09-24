"""TMDB search/discover + file search across sources with AI scoring."""

from app.core.module import Module
from app.modules.search.details import SOURCE_FILE_DETAILS
from app.modules.search.router import router

module = Module(name="search", title="Hledání", order=10, routers=[router], migrations=[SOURCE_FILE_DETAILS])
