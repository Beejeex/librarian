# Phase 7 — Tests

Port and adapt from `ref/tests/`. Use Librarian's existing conftest.py patterns
(in-memory SQLite, tmp_path fixtures, respx mocks).

## Tasks
- [ ] `tests/test_copier.py` — copy_file success, subtitle discovery, quota check hit/pass
- [ ] `tests/test_scheduler.py` — first-run index, normal poll new item, upgrade detection,
      quota hit leaves item pending, semaphore limits concurrency
- [ ] `tests/test_watcher.py` — file delete event marks TrackedItem copied→finished
- [ ] `tests/test_api.py` — extend existing file with tracker endpoints:
      approve, skip, reset, approve-all, poll trigger, reset-first-run
- [ ] `tests/test_radarr.py` — add tests for fetch_tag_ids, fetch_tagged_movies
- [ ] `tests/test_sonarr.py` — add tests for fetch_tag_ids, fetch_tagged_series, fetch_episode_files
- [ ] `tests/conftest.py` — add TrackedItem fixtures, mock_share tmp_path fixture
