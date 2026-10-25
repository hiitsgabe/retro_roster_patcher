"""The shared head-shot cache: downloaded once, never a page that is not a
picture; and the measuring pass every game shares."""

import pytest

from retro_roster_patcher.core.headshots import load_headshot, measure_faces

PNG = b"\x89PNG\r\n\x1a\n" + b"body"
JPEG = b"\xff\xd8\xff" + b"body"


def test_a_head_shot_is_downloaded_once_and_cached(tmp_path):
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        return PNG

    for _ in range(2):
        assert load_headshot(tmp_path, "u", transport) == PNG
    assert calls == ["u"]
    assert len(list((tmp_path / "headshots").iterdir())) == 1


def test_a_jpeg_is_a_head_shot_too(tmp_path):
    assert load_headshot(tmp_path, "u", lambda url, headers, timeout: JPEG) == JPEG


def test_an_error_page_or_a_failure_is_no_head_shot_and_is_not_cached(tmp_path):
    def page(url, headers, timeout):
        return b"<html>"

    def broken(url, headers, timeout):
        raise OSError("down")

    assert load_headshot(tmp_path, "u", page) is None
    assert load_headshot(tmp_path, "u", broken) is None
    assert not (tmp_path / "headshots").exists()


def test_a_cached_error_page_is_refetched(tmp_path):
    load_headshot(tmp_path, "u", lambda url, headers, timeout: PNG)
    (cached,) = (tmp_path / "headshots").iterdir()
    cached.write_bytes(b"<html>")
    assert load_headshot(tmp_path, "u", lambda url, headers, timeout: PNG) == PNG
    assert cached.read_bytes() == PNG


def test_a_cache_that_cannot_be_written_still_gives_the_picture(tmp_path):
    (tmp_path / "headshots").write_bytes(b"a file where the folder should be")
    assert load_headshot(tmp_path, "u", lambda url, headers, timeout: PNG) == PNG


def _picture(face, hair=None):
    pytest.importorskip("PIL")
    from tests.fixtures.synthetic_headshot import headshot

    return headshot(face, hair=hair)


def test_measure_faces_downloads_each_picture_once_and_measures_both(tmp_path):
    pic = _picture((225, 180, 150), hair=(22, 16, 12))
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        return pic

    faces = measure_faces(tmp_path, {1: "a", 2: "a", 3: "b"}, transport)
    assert calls.count("a") == 1 and calls.count("b") == 1
    assert faces[1] == faces[2] == faces[3]
    assert faces[1].hair == "black" and faces[1].ita is not None and faces[1].ita > 30


def test_measure_faces_skips_what_is_not_a_face(tmp_path):
    logo = _picture((128, 128, 128))  # a grey blob: no face

    def transport(url, headers, timeout):
        return logo if url == "a" else b"<html>"

    assert measure_faces(tmp_path, {1: "a", 2: "b"}, transport) == {}


def test_measure_faces_survives_a_picture_pillow_cannot_read(tmp_path):
    assert measure_faces(tmp_path, {1: "a"}, lambda u, h, t: PNG) == {}


def test_measure_faces_says_so_without_pillow(tmp_path, monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    lines = []
    assert measure_faces(tmp_path, {1: "a"}, lambda u, h, t: PNG, lines.append) == {}
    assert lines == ["Pillow is not installed; player looks not taken from head shots"]


def test_measure_faces_with_nothing_to_measure_is_quiet(tmp_path):
    lines = []
    assert measure_faces(tmp_path, {}, lambda u, h, t: PNG, lines.append) == {}
    assert lines == []


def test_measure_faces_reads_the_beard_too(tmp_path):
    pytest.importorskip("PIL")
    from tests.fixtures.synthetic_headshot import headshot

    bearded = headshot((225, 180, 150), hair=(22, 16, 12), beard=True)
    shaven = headshot((225, 180, 150), hair=(22, 16, 12))

    def transport(url, headers, timeout):
        return bearded if url == "a" else shaven

    faces = measure_faces(tmp_path, {1: "a", 2: "b"}, transport)
    assert (faces[1].beard, faces[2].beard) == (True, False)
