import fcntl

from lectra import lecture


def test_podcast_lock_blocks_a_second_holder_until_released(tmp_path, monkeypatch):
    monkeypatch.setattr(lecture, "PODCAST_LOCK_FILE", tmp_path / "podcast.lock")

    with lecture.podcast_lock():
        with open(tmp_path / "podcast.lock", "w") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                acquired = False

    assert acquired is False
