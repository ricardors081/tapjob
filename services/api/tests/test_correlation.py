from tapjob_api.core.correlation import (
    CORRELATION_ID_PATTERN,
    is_valid_correlation_id,
    resolve_correlation_id,
)


def test_valid_candidate_is_returned_unchanged() -> None:
    assert resolve_correlation_id("abc-123.XYZ_9", fallback="fallback") == "abc-123.XYZ_9"


def test_invalid_candidate_falls_back() -> None:
    assert resolve_correlation_id("has spaces", fallback="fallback-value") == "fallback-value"


def test_none_candidate_falls_back() -> None:
    assert resolve_correlation_id(None, fallback="fallback-value") == "fallback-value"


# -- untrusted runtime-typed candidates (2026 review, Finding 3) --------------------------------
# `candidate` is typed `object`, not `str | None`: a Celery message header is read out of a
# broker message's untyped mapping, so a malformed or hostile producer can put any JSON-ish type
# under the same header name. None of these may raise -- each must degrade to `fallback`.


def test_integer_candidate_falls_back_without_raising() -> None:
    assert resolve_correlation_id(123, fallback="fallback-value") == "fallback-value"
    assert is_valid_correlation_id(123) is False


def test_list_candidate_falls_back_without_raising() -> None:
    assert resolve_correlation_id(["a", "b"], fallback="fallback-value") == "fallback-value"
    assert is_valid_correlation_id(["a", "b"]) is False


def test_dict_candidate_falls_back_without_raising() -> None:
    assert resolve_correlation_id({"a": 1}, fallback="fallback-value") == "fallback-value"
    assert is_valid_correlation_id({"a": 1}) is False


def test_bool_candidate_falls_back_without_raising() -> None:
    # bool is a str subtype's sibling pitfall (it IS an int, not a str) -- must not be mistaken
    # for a valid string candidate.
    assert resolve_correlation_id(True, fallback="fallback-value") == "fallback-value"
    assert is_valid_correlation_id(True) is False


def test_pattern_rejects_newline_injection() -> None:
    assert CORRELATION_ID_PATTERN.fullmatch("abc\ndef") is None


def test_pattern_rejects_over_length_values() -> None:
    assert CORRELATION_ID_PATTERN.fullmatch("a" * 129) is None


def test_pattern_accepts_the_maximum_length() -> None:
    assert CORRELATION_ID_PATTERN.fullmatch("a" * 128) is not None


def test_pattern_rejects_empty_string() -> None:
    assert CORRELATION_ID_PATTERN.fullmatch("") is None
