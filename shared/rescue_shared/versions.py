"""Version string formats, defined once.

rules_version  rules@<rules_sha>+bin:<bin_hash>
    rules_sha: git SHA (7-40 lowercase hex) of the rules source.
    bin_hash:  hash (8-64 lowercase hex) of the engine binary/artifact that implements them.
    Example: rules@a1b2c3d+bin:9f8e7d6c5b4a3f21
model_version  hf:<org>/<repo>@<commit_sha>    (a pinned Hugging Face revision, never a branch name)
               stub:<name>                     (a test double, such as the FixtureExtractor)
    Example: hf:rescue-org/rescue-tagger@4d5e6f7a

rules_version() is the only place the string is built: engine E1/E5 and cloud replay C9 call it.
The JSON Schemas in contracts/common.schema.json carry the same patterns.
"""

from __future__ import annotations

import re

RULES_VERSION_RE = re.compile(
    r"^rules@(?P<rules_sha>[0-9a-f]{7,40})\+bin:(?P<bin_hash>[0-9a-f]{8,64})$"
)
MODEL_VERSION_RE = re.compile(
    r"^(?:hf:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@(?P<revision>[0-9a-f]{7,40})|stub:(?P<stub>[a-z0-9_-]+))$"
)
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_HASH_RE = re.compile(r"^[0-9a-f]{8,64}$")

STUB_MODEL_VERSION = "stub:fixture-extractor"


def rules_version(rules_sha: str, bin_hash: str) -> str:
    """Build a rules_version string, validating both parts."""
    if not _SHA_RE.fullmatch(rules_sha):
        raise ValueError(f"rules_sha must be 7-40 lowercase hex characters: {rules_sha!r}")
    if not _HASH_RE.fullmatch(bin_hash):
        raise ValueError(f"bin_hash must be 8-64 lowercase hex characters: {bin_hash!r}")
    return f"rules@{rules_sha}+bin:{bin_hash}"


def parse_rules_version(value: str) -> tuple[str, str]:
    """Return (rules_sha, bin_hash)."""
    match = RULES_VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError(f"not a rules_version: {value!r}")
    return match["rules_sha"], match["bin_hash"]


def model_version(repo: str, revision: str) -> str:
    """Build the model_version of a pinned Hugging Face revision, e.g. repo='org/name'."""
    value = f"hf:{repo}@{revision}"
    if not MODEL_VERSION_RE.fullmatch(value) or value.startswith("stub:"):
        raise ValueError(f"invalid Hugging Face repo/revision: {repo!r}, {revision!r}")
    return value


def is_rules_version(value: object) -> bool:
    return isinstance(value, str) and RULES_VERSION_RE.fullmatch(value) is not None


def is_model_version(value: object) -> bool:
    return isinstance(value, str) and MODEL_VERSION_RE.fullmatch(value) is not None
