"""
Unit tests for file copy / hardlink logic and quota helpers (app/copier.py).

Uses tmp_path so no real share or media mount is needed.
"""

import os
import pytest
from unittest.mock import MagicMock
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from app.copier import (
    copy_file,
    find_subtitle_files,
    build_movie_share_path,
    build_episode_share_path,
    get_file_size,
    get_quota_usage,
    check_quota,
    get_share_stats,
)
from app.models import AppConfig, TrackedItem


# ---------------------------------------------------------------------------
# File copy tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_copy_creates_file_at_destination(tmp_share):
    """copy_file in 'copy' mode should place an identical file at the destination."""
    src = tmp_share["sample_file"]
    dest = os.path.join(tmp_share["share"], "Movie (2024)", "Movie.mkv")

    await copy_file(src, dest, mode="copy")

    assert os.path.isfile(dest)
    assert open(dest, "rb").read() == open(src, "rb").read()


@pytest.mark.asyncio
async def test_copy_creates_intermediate_directories(tmp_share):
    """copy_file must create any missing parent directories automatically."""
    src = tmp_share["sample_file"]
    dest = os.path.join(tmp_share["share"], "deep", "nested", "dir", "file.mkv")

    await copy_file(src, dest, mode="copy")

    assert os.path.isfile(dest)


@pytest.mark.asyncio
async def test_hardlink_creates_link(tmp_share):
    """copy_file in 'hardlink' mode should create a hard link (same inode)."""
    src = tmp_share["sample_file"]
    dest = os.path.join(tmp_share["share"], "linked.mkv")

    await copy_file(src, dest, mode="hardlink")

    assert os.path.isfile(dest)
    # Hard links share the same inode number
    assert os.stat(src).st_ino == os.stat(dest).st_ino


@pytest.mark.asyncio
async def test_subtitle_files_are_copied_alongside_video(tmp_share):
    """copy_file should automatically copy companion subtitle files to the destination dir."""
    src = tmp_share["sample_file"]  # e.g. .../media/Movie.Title.2024.mkv
    src_stem = os.path.splitext(os.path.basename(src))[0]
    src_dir = os.path.dirname(src)

    # Place subtitle files next to the source video
    srt_en = os.path.join(src_dir, f"{src_stem}.en.srt")
    srt_fr = os.path.join(src_dir, f"{src_stem}.fr.srt")
    unrelated = os.path.join(src_dir, "other_movie.en.srt")
    for path in (srt_en, srt_fr, unrelated):
        open(path, "w").write("subtitle content")

    dest = os.path.join(tmp_share["share"], "Movie (2024)", os.path.basename(src))
    await copy_file(src, dest, mode="copy")

    dest_dir = os.path.join(tmp_share["share"], "Movie (2024)")
    assert os.path.isfile(os.path.join(dest_dir, f"{src_stem}.en.srt"))
    assert os.path.isfile(os.path.join(dest_dir, f"{src_stem}.fr.srt"))
    # Unrelated subtitle should NOT be copied
    assert not os.path.isfile(os.path.join(dest_dir, "other_movie.en.srt"))


def test_find_subtitle_files_matches_by_stem(tmp_share):
    """find_subtitle_files should return subs matching the video stem, not unrelated files."""
    src = tmp_share["sample_file"]
    stem = os.path.splitext(os.path.basename(src))[0]
    src_dir = os.path.dirname(src)

    for name in (f"{stem}.srt", f"{stem}.en.ass", f"{stem}.forced.srt", "other.srt", "readme.txt"):
        open(os.path.join(src_dir, name), "w").write("")

    found = {os.path.basename(p) for p in find_subtitle_files(src)}
    assert f"{stem}.srt" in found
    assert f"{stem}.en.ass" in found
    assert f"{stem}.forced.srt" in found
    assert "other.srt" not in found
    assert "readme.txt" not in found


@pytest.mark.asyncio
async def test_copy_raises_on_missing_source(tmp_share):
    """copy_file should raise OSError when the source file does not exist."""
    with pytest.raises(OSError):
        await copy_file("/nonexistent/path/file.mkv", tmp_share["share"] + "/out.mkv")


def test_build_movie_share_path():
    """Movie share path should follow '<share>/<Title (Year)>/<filename>' format."""
    path = build_movie_share_path("/share", "Inception", 2010, "Inception.mkv")
    assert path == "/share/Inception (2010)/Inception.mkv"


def test_build_episode_share_path():
    """Episode share path should follow '<share>/<Series>/Season XX/<filename>' format."""
    path = build_episode_share_path("/share", "Breaking Bad", 1, "BB.S01E01.mkv")
    assert path == "/share/Breaking Bad/Season 01/BB.S01E01.mkv"


# ---------------------------------------------------------------------------
# Quota helper tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def quota_session():
    """In-memory SQLite session pre-populated with sample TrackedItems for quota tests."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        # Two backlog copied items (500 MB each)
        for i in range(2):
            session.add(TrackedItem(
                source="radarr", media_type="movie", source_id=i,
                title=f"Backlog {i}", file_path=f"/src/{i}.mkv", share_path=f"/share/{i}.mkv",
                status="copied", is_backlog=True, file_size_bytes=500 * 1024 * 1024,
            ))
        # One new (non-backlog) copied item (200 MB)
        session.add(TrackedItem(
            source="radarr", media_type="movie", source_id=10,
            title="New Item", file_path="/src/new.mkv", share_path="/share/new.mkv",
            status="copied", is_backlog=False, file_size_bytes=200 * 1024 * 1024,
        ))
        session.commit()
        yield session


def test_get_file_size_existing(tmp_share):
    """get_file_size returns correct size for an existing file."""
    size = get_file_size(tmp_share["sample_file"])
    assert size == 15  # b"fake video data" is 15 bytes


def test_get_file_size_missing():
    """get_file_size returns 0 if the file doesn't exist."""
    assert get_file_size("/nonexistent/file.mkv") == 0


def test_get_quota_usage_backlog(quota_session):
    """get_quota_usage for backlog should sum the two 500 MB items."""
    usage = get_quota_usage(quota_session, is_backlog=True)
    assert usage["file_count"] == 2
    assert usage["size_bytes"] == 2 * 500 * 1024 * 1024


def test_get_quota_usage_new(quota_session):
    """get_quota_usage for new items should return the single 200 MB item."""
    usage = get_quota_usage(quota_session, is_backlog=False)
    assert usage["file_count"] == 1
    assert usage["size_bytes"] == 200 * 1024 * 1024


def test_check_quota_passes_when_unlimited(quota_session):
    """check_quota always returns True when both caps are 0 (unlimited)."""
    config = MagicMock(max_share_size_gb=0, max_share_files=0)
    assert check_quota(quota_session, config, is_backlog=True) is True


def test_check_quota_fails_when_size_exceeded(quota_session):
    """check_quota returns False when backlog usage exceeds the size cap."""
    # Backlog cap = 2 GB * 0.6 = 1.2 GB; usage = 1 GB; should pass
    config = MagicMock(max_share_size_gb=2.0, max_share_files=0)
    assert check_quota(quota_session, config, is_backlog=True) is True

    # Backlog cap = 1 GB * 0.6 = 0.6 GB; usage = 1 GB; should fail
    config2 = MagicMock(max_share_size_gb=1.0, max_share_files=0)
    assert check_quota(quota_session, config2, is_backlog=True) is False


def test_check_quota_new_uses_full_cap(quota_session):
    """New items can use the full total cap, not just 40% — backlog headroom is available."""
    # Total usage = 1 GB backlog + 200 MB new = 1.2 GB
    # Total cap = 2 GB; new item (prospective 600 MB) would bring total to 1.8 GB < 2 GB — pass
    config = MagicMock(max_share_size_gb=2.0, max_share_files=0)
    prospective = 600 * 1024 * 1024
    assert check_quota(quota_session, config, is_backlog=False, prospective_bytes=prospective) is True

    # Prospective 900 MB would push total to 2.1 GB > 2 GB cap — fail
    prospective_over = 900 * 1024 * 1024
    assert check_quota(quota_session, config, is_backlog=False, prospective_bytes=prospective_over) is False


def test_check_quota_uses_prospective_size(quota_session):
    """check_quota should reject a copy if current + prospective_bytes would exceed the cap."""
    # Backlog cap = 2 GB * 0.6 = 1.2 GB; usage = 1 GB — fits without prospective
    config = MagicMock(max_share_size_gb=2.0, max_share_files=0)
    assert check_quota(quota_session, config, is_backlog=True) is True

    # Adding a 300 MB file would push usage to 1.3 GB > 1.2 GB cap — should fail
    prospective = 300 * 1024 * 1024
    assert check_quota(quota_session, config, is_backlog=True, prospective_bytes=prospective) is False


def test_check_quota_fails_when_file_count_exceeded(quota_session):
    """check_quota returns False when backlog file count exceeds the cap."""
    # Backlog cap = 10 * 0.6 = 6; usage = 2; should pass
    config = MagicMock(max_share_size_gb=0, max_share_files=10)
    assert check_quota(quota_session, config, is_backlog=True) is True

    # Backlog cap = 3 * 0.6 = 1 (int); usage = 2; should fail
    config2 = MagicMock(max_share_size_gb=0, max_share_files=3)
    assert check_quota(quota_session, config2, is_backlog=True) is False


def test_get_share_stats(tmp_share):
    """get_share_stats walks the directory and counts files and total bytes."""
    stats = get_share_stats(tmp_share["media"])
    assert stats["file_count"] == 1
    assert stats["size_bytes"] == 15  # b"fake video data"
    assert stats["size_gb"] >= 0

