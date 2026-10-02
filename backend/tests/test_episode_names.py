"""Telling an episode by its name (library/episode_names): seasons, specials, length, junk, near spellings."""

from app.modules.library import episode_names as en

CAT = {
    (1, 82): {"cs": "Přátelé navěky", "en": "Friends to the End", "runtime": 22},
    (2, 1): {"cs": "Panika v Oblázkovém městě", "en": "Pallet Party Panic", "runtime": 22},
    (2, 2): {"cs": "Hrůza ve vzduchu", "en": "A Scare in the Air", "runtime": 22},
    (2, 34): {"cs": "Podzemní zápas", "en": "Underground Round", "runtime": 22},
    (14, 48): {"cs": "Zápas o podzemní dráhu!", "en": "Battling at Full Volume!", "runtime": 22},
    (0, 27): {"cs": "Cedule", "en": "The Sign", "runtime": 28},
    (3, 49): {"cs": "Překvápko", "en": "Surprise!", "runtime": 7},
    (5, 5): {"cs": "5. epizoda", "en": "Episode 5", "runtime": 22},
}


def test_own_season_then_next_one_then_specials_with_the_length():
    assert en.best("Panika v Oblázkovém městě", CAT, 2) == ((2, 1), True)
    assert en.best("Přátelé navěky", CAT, 2) == ((1, 82), True)                 # the season before
    assert en.best("Podzemní zápas", CAT, 2) == ((2, 34), True)                 # the same name before a longer one
    assert en.best("Cedule", CAT, 3, duration_s=29 * 60) == ((0, 27), True)     # a special, its length fits
    assert en.best("Cedule", CAT, 3, duration_s=7 * 60) == (None, False)        # the length does not
    assert en.best("Episode 5", CAT, 5) == (None, False)                        # generic names are no names


def test_near_spelling_only_for_longer_words():
    cat = {(2, 3): {"cs": "Ikeova obřízka"}, (1, 10): {"cs": "Damián"}, (12, 9): {"cs": "Homr"}}
    assert en.best("Ikova obrizka", cat, 2) == ((2, 3), True)
    assert en.best("Dama s mrtvym siamskym embryem", cat, 2) == (None, False)
    assert en.best("Super Homer", cat, 21) == (None, False)


def test_own_names_from_the_first_name_and_junk_is_none():
    origins = en.origins([("/a/04x05 V očích to není.avi", "/b/Kutil Tim - S04E05 - Není tak zlý [480p].avi"),
                          ("/b/Kutil Tim - S04E05 - Není tak zlý [480p].avi", "/c/Kutil Tim - S04E05 - Není tak zlý [480p].avi")])
    assert origins["/c/Kutil Tim - S04E05 - Není tak zlý [480p].avi"] == "/a/04x05 V očích to není.avi"
    files = [(f"/s/{i}.mkv", f"/s/Cestovatelé.časem.02x0{i}.DVB-C.CZ.avi") for i in range(1, 5)] + \
            [("/s/x.mkv", "/s/The Flash-S01E13-AtomovÃ½ muÅ¾-1080p.mkv"), ("/s/y.mkv", "/s/S01E02 Posilovač 4000.mkv")]
    from app.modules.library.importer import _bare_title, _title_in_name
    own = en.own_titles(files, lambda n: _title_in_name(n) or _bare_title(n))
    assert own["/s/1.mkv"] == "" and own["/s/x.mkv"] == "" and own["/s/y.mkv"] == "Posilovač 4000"


def test_part_in_brackets():
    from app.modules.library.tv_inventory import title_score
    assert title_score("Heart of Archness Part I", "Heart of Archness (2)") == 0
    assert title_score("Heart of Archness Part I", "Heart of Archness (1)") == 1


def test_origins_of_files_trading_places_in_one_batch():
    rows = [("/s/78.Prvni.avi", "/s/E78 Prvni.avi", "b1"), ("/s/77.Napinavy.avi", "/s/E77 Napinavy.avi", "b1"),
            ("/s/E78 Prvni.avi", "/s/E77 Prvni.avi", "b2"), ("/s/E77 Napinavy.avi", "/s/E79 Napinavy.avi", "b2")]
    back = en.origins(rows)
    assert back["/s/E77 Prvni.avi"] == "/s/78.Prvni.avi" and back["/s/E79 Napinavy.avi"] == "/s/77.Napinavy.avi"
