from app.clients.groq_scorer import _is_obviously_irrelevant, _parse_scores
from app.models.schemas import ScorableFile


def _file(name, size=2_000_000_000):
    return ScorableFile(index=0, name=name, size=size, source="webshare", ident="x")


def test_parse_compact_format_with_noise_around():
    content = 'Here you go:\n```json\n[[0, "1080p", 1, 95], [1, "SD", 0, 10]]\n```'
    assert _parse_scores(content) == [(0, "1080p", True, 95), (1, "SD", False, 10)]


def test_parse_legacy_object_format():
    content = '[{"index": 2, "quality": "720p", "is_dubbed": true, "relevance_score": 80}]'
    assert _parse_scores(content) == [(2, "720p", True, 80)]


def test_obviously_irrelevant_files_skip_ai():
    assert _is_obviously_irrelevant(_file("Matrix.1999.CZ.titulky.srt", 90_000))
    assert _is_obviously_irrelevant(_file("Matrix 1999 sample.mkv", 50_000_000))
    assert not _is_obviously_irrelevant(_file("Matrix.1999.1080p.mkv"))
    # a big file with "sample" in the title is a real movie
    assert not _is_obviously_irrelevant(_file("Free.Sample.2016.1080p.mkv", 3_000_000_000))


def test_title_match_uses_best_of_all_names():
    from app.clients.groq_scorer import _title_match_score

    assert _title_match_score("Cosy Dens 1999", "Pelíšky (1999) CZ.mkv") < 70
    assert _title_match_score("Cosy Dens 1999 / Pelíšky", "Pelíšky (1999) CZ.mkv") >= 90
