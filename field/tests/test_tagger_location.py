from rescue_field.tagger.assemble import assemble_location

TEXT = "help at 12 Main St now"
SPAN = {"start": 8, "end": 18}


def probs(addr: float) -> dict[str, float]:
    return {"landmark": 0.0, "address": addr, "coords": 0.0, "none": 0.0}


def test_weak_span_caps_confidence():
    loc = assemble_location(TEXT, {**SPAN, "confidence": 0.4}, probs(0.95))
    assert loc["confidence"] == 0.4 and loc["text"] == "12 Main St"


def test_weak_type_head_wins():
    loc = assemble_location(TEXT, {**SPAN, "confidence": 0.9}, probs(0.7))
    assert loc["confidence"] == 0.7


def test_no_span_gives_none():
    p = {"landmark": 0.0, "address": 0.1, "coords": 0.0, "none": 0.8}
    loc = assemble_location(TEXT, None, p)
    assert loc == {"text": None, "type": "none", "confidence": 0.8}
