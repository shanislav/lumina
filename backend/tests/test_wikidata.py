"""Wikidata entity → film metadata (no network)."""

from app.clients.wikidata import parse_entity

PAR_PARMENU = {
    "id": "Q9318176",
    "labels": {"cs": {"value": "Pár Pařmenů"}, "en": {"value": "Pár Pařmenů"}},
    "descriptions": {"en": {"value": "2004 film"}},
    "aliases": {"cs": [{"value": "Pár Pařmenů: Společenstvo žlutého tentononcu"}]},
    "sitelinks": {"cswiki": {"title": "Pár Pařmenů"}},
    "claims": {
        "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q11424"}}}}],
        "P577": [{"mainsnak": {"datavalue": {"value": {"time": "+2004-01-01T00:00:00Z"}}}}],
        "P2047": [{"mainsnak": {"datavalue": {"value": {"amount": "+178", "unit": "http://www.wikidata.org/entity/Q7727"}}}}],
        "P2529": [{"mainsnak": {"datavalue": {"value": "12345"}}}],
    },
}


def test_film_entity():
    f = parse_entity(PAR_PARMENU)
    assert f["title"] == "Pár Pařmenů" and f["year"] == 2004 and f["runtime"] == 178
    assert "Pár Pařmenů: Společenstvo žlutého tentononcu" in f["titles"]
    assert f["csfd_id"] == "12345" and f["wiki_title"] == "Pár Pařmenů" and f["poster_url"] is None


def test_not_a_film_is_skipped():
    person = {"id": "Q1", "labels": {"cs": {"value": "Někdo"}}, "descriptions": {"cs": {"value": "český herec"}},
              "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]}}
    assert parse_entity(person) is None
