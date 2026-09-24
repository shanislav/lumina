"""TMDB search/discover + the file search API (the search itself is app/core/offers)."""

from app.core.module import Module
from app.core.offers.details import SOURCE_FILE_DETAILS
from app.modules.search.router import router

module = Module(name="search", title="Hledání", order=10, routers=[router], migrations=[SOURCE_FILE_DETAILS])
