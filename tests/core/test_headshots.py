"""The shared head-shot cache: downloaded once, never a page that is not a picture."""

from retro_roster_patcher.core.headshots import load_headshot

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
