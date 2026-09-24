"""Offers: finding a film's files on the sources and judging them — shared by every module
that needs it (search UI, background upgrade checks, later the scheduler / automation).

- search.py    find_offers(): queries → sources → rules (film, quality, languages) → AI for unclear
               verify_offers(): real details from the sources for the likely ones (throttled, cached)
               is_upgrade(): is an offer better than an owned version (same rule as the UI)
- evaluate.py  one file → film verdict + quality score + languages
- details.py   WebShare file_info / FastShare page details, cached per file (90 days)
"""
