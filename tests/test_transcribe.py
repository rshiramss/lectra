from pathlib import Path

import pytest

from lectra.transcribe import Word, load_words, save_words, words_from_result


def test_words_from_result_flattens_segments_and_strips_whitespace():
    result = {
        "segments": [
            {"words": [{"word": " Hello", "start": 0.0, "end": 0.4}]},
            {"words": [{"word": " world.", "start": 0.5, "end": 0.9}]},
        ]
    }

    words = words_from_result(result)

    assert words == [Word("Hello", 0.0, 0.4), Word("world.", 0.5, 0.9)]


def test_words_from_result_skips_segments_without_words():
    result = {"segments": [{"text": "silence"}, {"words": []}]}

    assert words_from_result(result) == []


def test_save_then_load_round_trips(tmp_path: Path):
    words = [Word("flip", 1.0, 1.2), Word("flop", 1.3, 1.6)]
    target = tmp_path / "words.json"

    save_words(words, target)

    assert load_words(target) == words


@pytest.mark.slow
def test_transcribe_real_audio_returns_ordered_timestamps():
    from lectra.transcribe import transcribe_words

    audio = Path(__file__).parent / "fixtures" / "binary_counter.aiff"

    words = transcribe_words(audio, model="mlx-community/whisper-tiny")

    assert any(word.text.lower().startswith("counter") for word in words)
    assert all(a.start <= b.start for a, b in zip(words, words[1:]))
