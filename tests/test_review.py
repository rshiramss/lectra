import numpy as np

from lectra.manimgen import build_densify_prompt
from lectra.review import content_area_fraction, is_sparse

HEIGHT, WIDTH = 100, 200


def frame_with_block(top, bottom, left, right):
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[top:bottom, left:right] = 255
    return frame


def test_blank_frame_has_no_content():
    assert content_area_fraction(np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)) == 0.0


def test_content_area_is_the_bounding_box_of_non_background_pixels():
    frame = frame_with_block(top=0, bottom=50, left=0, right=100)

    assert content_area_fraction(frame) == 0.25


def test_dim_pixels_below_threshold_are_treated_as_background():
    frame = np.full((HEIGHT, WIDTH, 3), 5, dtype=np.uint8)

    assert content_area_fraction(frame) == 0.0


def test_a_strip_at_the_top_is_sparse_and_a_filled_frame_is_not():
    assert is_sparse(content_area_fraction(frame_with_block(0, 15, 10, 190)))
    assert not is_sparse(content_area_fraction(frame_with_block(5, 95, 10, 190)))


def test_densify_prompt_includes_the_source_and_asks_to_use_the_whole_canvas():
    prompt = build_densify_prompt("class Slide3Scene: pass", 3, coverage=0.15)

    assert "class Slide3Scene: pass" in prompt and "15%" in prompt


def test_clip_coverage_samples_within_the_clip_even_when_the_scene_is_shorter_than_its_slot(tmp_path):
    import subprocess

    from lectra.review import clip_coverage

    short_clip = tmp_path / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=white:s=64x64:d=2:r=10", "-pix_fmt", "yuv420p", str(short_clip)],
        check=True,
    )

    assert clip_coverage(short_clip) == 1.0
