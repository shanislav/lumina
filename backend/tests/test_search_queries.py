from app.modules.search.router import _ddl_queries, _is_video_name


def test_ddl_queries_short_full_and_english_title():
    assert _ddl_queries("Podfukáři 3: Nové kouzlo 2025", "Now You See Me: Now You Don't", "Now You See Me: Now You Don't") == \
        ["Podfukáři 3", "Podfukáři 3 Nové kouzlo", "Now You See Me Now You Dont"]


def test_ddl_queries_without_subtitle_and_duplicates():
    assert _ddl_queries("Pelíšky 1999", "Pelíšky", "Cosy Dens") == ["Pelíšky", "Cosy Dens"]
    assert _ddl_queries("Matrix 1999", "The Matrix", "The Matrix") == ["Matrix", "The Matrix"]


def test_only_video_files_from_ddl():
    names = ["a.mkv", "b.rar", "c.torrent", "d.srt", "e.iso", "f.part1.rar", "g.MP4", "h.001", "i.zip", "no-extension"]
    assert [n for n in names if _is_video_name(n)] == ["a.mkv", "g.MP4", "no-extension"]


def test_fastshare_names_are_unescaped(monkeypatch):
    import asyncio
    from app.clients.fastshare import FastShareClient

    class Resp:
        def json(self):
            return {"search": {"file": [{"id": "1", "filename": "Now.You.See.Me.Now.You.Don&#39;t.2025.mkv",
                                         "data": {"value": "1"}}]}}

    client = FastShareClient("u", "p")
    client._hash = "x"

    async def fake_get(*args, **kwargs):
        return Resp()
    monkeypatch.setattr(client._http, "get", fake_get)
    files = asyncio.run(client.search("x"))
    assert files[0].name == "Now.You.See.Me.Now.You.Don't.2025.mkv"


def test_year_guard():
    from app.core.film_match import years_mismatch as _years_mismatch
    from app.modules.search.evaluate import year_of

    assert year_of("Cosy Dens 1999") == 1999
    assert _years_mismatch("Den co den 2018 BluRay 1080p x264CZ EN DTS.mkv", 1999)
    assert not _years_mismatch("Pelíšky (1999).avi", 1999)
    assert not _years_mismatch("Pelíšky.avi", 1999)                         # no year → no opinion
    assert not _years_mismatch("1917.2019.1080p.BluRay.mkv", 2019)          # title with a number
    assert not _years_mismatch("2001.A.Space.Odyssey.1968.mkv", 1968)
    assert not _years_mismatch("Matrix.2000.remaster.mkv", 1999)            # ±1
