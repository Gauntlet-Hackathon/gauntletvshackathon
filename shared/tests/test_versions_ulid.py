import pytest

from rescue_shared import ulid, versions


def test_new_ulid_is_deterministic_with_fixed_inputs():
    value = ulid.new_ulid(timestamp_ms=1_700_000_000_000, randomness=0)
    assert len(value) == 26 and ulid.is_ulid(value)
    assert ulid.ulid_timestamp_ms(value) == 1_700_000_000_000
    assert value.endswith("0" * 16)


def test_ulids_sort_by_time():
    first = ulid.new_ulid(timestamp_ms=1000, randomness=5)
    second = ulid.new_ulid(timestamp_ms=1001, randomness=1)
    assert first < second


def test_is_ulid_rejects_bad_values():
    assert not ulid.is_ulid("short")
    assert not ulid.is_ulid("01JCB00000000000000000RQ0U")  # U is not in the alphabet
    assert not ulid.is_ulid("81JCB00000000000000000RQ01")  # time overflow
    assert not ulid.is_ulid(None)


def test_new_ulid_range_checks():
    with pytest.raises(ValueError):
        ulid.new_ulid(timestamp_ms=-1)
    with pytest.raises(ValueError):
        ulid.new_ulid(randomness=1 << 80)


def test_rules_version_round_trip():
    value = versions.rules_version("a1b2c3d", "9f8e7d6c5b4a3f21")
    assert value == "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
    assert versions.parse_rules_version(value) == ("a1b2c3d", "9f8e7d6c5b4a3f21")
    assert versions.is_rules_version(value)


@pytest.mark.parametrize(
    "sha, bin_hash", [("A1B2C3D", "9f8e7d6c"), ("abc", "9f8e7d6c"), ("a1b2c3d", "xyz")]
)
def test_rules_version_rejects_bad_parts(sha, bin_hash):
    with pytest.raises(ValueError):
        versions.rules_version(sha, bin_hash)


def test_model_version_formats():
    value = versions.model_version("rescue-org/rescue-tagger", "4d5e6f7a8b")
    assert value == "hf:rescue-org/rescue-tagger@4d5e6f7a8b"
    assert versions.is_model_version(value)
    assert versions.is_model_version(versions.STUB_MODEL_VERSION)
    assert not versions.is_model_version("hf:rescue-org/rescue-tagger@main")
    with pytest.raises(ValueError):
        versions.model_version("rescue-org/rescue-tagger", "main")
