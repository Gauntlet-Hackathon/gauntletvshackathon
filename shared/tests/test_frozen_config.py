from rescue_shared import frozen_config as fc


def test_hash_is_stable_and_order_insensitive_for_thresholds():
    c = fc.load_config()
    h = fc.config_hash(c["thresholds"], c["hoax_markers"], c["streets"])
    reordered = dict(reversed(list(c["thresholds"].items())))
    assert fc.config_hash(reordered, c["hoax_markers"], c["streets"]) == h
    assert len(h) == fc.HASH_CHARS


def test_any_value_or_entry_changes_the_hash():
    c = fc.load_config()
    h = fc.config_hash(c["thresholds"], c["hoax_markers"], c["streets"])
    assert fc.config_hash({**c["thresholds"], "need_hours_over": 25}, c["hoax_markers"],
                          c["streets"]) != h  # fmt: skip
    assert fc.config_hash(c["thresholds"], c["hoax_markers"][1:], c["streets"]) != h
    assert fc.config_hash(c["thresholds"], c["hoax_markers"], (*c["streets"], "X")) != h


def test_combined_bin_hash_depends_on_both_parts():
    assert fc.combined_bin_hash("a" * 16, "b" * 16) != fc.combined_bin_hash("a" * 16, "c" * 16)
    assert fc.combined_bin_hash("a" * 16, "b" * 16) != fc.combined_bin_hash("d" * 16, "b" * 16)


def test_config_diff_names_the_parts():
    c = fc.load_config()
    other = {**c, "streets": c["streets"][:-1]}
    assert fc.config_diff(c, other) == ["streets"]
    assert fc.config_diff(c, dict(c)) == []
