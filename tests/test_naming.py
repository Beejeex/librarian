"""
test_naming.py — Tests for app/naming.py.

Covers clean_title() edge cases and folder name builders for movies and TV series.
"""

import pytest

from app.naming import clean_title, movie_folder_name, series_folder_name


# ---------------------------------------------------------------------------
# clean_title
# ---------------------------------------------------------------------------
class TestCleanTitle:
    def test_colon_space_becomes_dash(self):
        assert clean_title("Avengers: Endgame") == "Avengers - Endgame"

    def test_standalone_colon_becomes_dash(self):
        assert clean_title("Batman:Returns") == "Batman-Returns"

    def test_question_mark_removed(self):
        assert clean_title("Who Am I?") == "Who Am I"

    def test_asterisk_removed(self):
        assert clean_title("Star*Wars") == "StarWars"

    def test_double_quotes_removed(self):
        assert clean_title('Say "Hello"') == "Say Hello"

    def test_angle_brackets_removed(self):
        assert clean_title("A<B>C") == "ABC"

    def test_pipe_removed(self):
        assert clean_title("A|B") == "AB"

    def test_backslash_removed(self):
        assert clean_title("A\\B") == "AB"

    def test_forward_slash_removed(self):
        assert clean_title("A/B") == "AB"

    def test_internal_dots_preserved_trailing_stripped(self):
        # Dots within the title are kept; trailing dot stripped by step 5
        assert clean_title("S.W.A.T.") == "S.W.A.T"

    def test_trailing_dot_stripped(self):
        # Strip trailing dots and spaces from the final result
        assert clean_title("End.") == "End"

    def test_multiple_spaces_collapsed(self):
        assert clean_title("Hello   World") == "Hello World"

    def test_leading_trailing_spaces_stripped(self):
        assert clean_title("  Hello  ") == "Hello"

    def test_no_change_needed(self):
        assert clean_title("What We Do in the Shadows") == "What We Do in the Shadows"

    def test_complex_title(self):
        assert clean_title("Avengers: Infinity War") == "Avengers - Infinity War"

    def test_multiple_colons(self):
        # Two colon-space sequences
        assert clean_title("A: B: C") == "A - B - C"


# ---------------------------------------------------------------------------
# movie_folder_name
# ---------------------------------------------------------------------------
class TestMovieFolderName:
    def test_basic(self):
        movie = {"title": "Dune", "year": 2021, "tmdbId": 438631}
        assert movie_folder_name(movie) == "Dune (2021) {tmdb-438631}"

    def test_with_colon(self):
        movie = {"title": "Avengers: Endgame", "year": 2019, "tmdbId": 299534}
        assert movie_folder_name(movie) == "Avengers - Endgame (2019) {tmdb-299534}"

    def test_special_chars(self):
        movie = {"title": "Se7en", "year": 1995, "tmdbId": 807}
        assert movie_folder_name(movie) == "Se7en (1995) {tmdb-807}"


# ---------------------------------------------------------------------------
# series_folder_name
# ---------------------------------------------------------------------------
class TestSeriesFolderName:
    def test_basic(self):
        series = {"title": "Breaking Bad", "year": 2008, "tvdbId": 81189}
        assert series_folder_name(series) == "Breaking Bad (2008) {tvdb-81189}"

    def test_with_colon(self):
        series = {"title": "Star Trek: Picard", "year": 2020, "tvdbId": 364093}
        assert series_folder_name(series) == "Star Trek - Picard (2020) {tvdb-364093}"
