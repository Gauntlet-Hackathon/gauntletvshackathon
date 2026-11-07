"""HFExtractor: the real SLM adapter (F11).

Loads HF_REPO@HF_REVISION (a pinned commit SHA, never a branch) from a local path or the local
HF cache only: no network at runtime. Assumption (H1a is still a template in docs/DECISIONS.md):
the model is generative and emits JSON shaped like contracts/payloads/extracted.schema.json;
`_map_output` is the single place to change once the real output format is known.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from rescue_field.slm import Extraction, validate_extracted

_SHA = re.compile(r"^[0-9a-f]{40}$")
Generate = Callable[[str], str]


def _check_revision(revision: str | None) -> str:
    if not revision or not _SHA.match(revision):
        raise ValueError("HF_REVISION must be a full 40-char commit SHA (never a branch like main)")
    return revision


def _map_output(text: str) -> dict[str, Any]:
    """Parse the model's raw output into an `extracted` payload (schema-validated by caller)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output contains no JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data


def _load_generate(repo: str, revision: str, local_path: str | None) -> Generate:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from transformers import AutoModelForCausalLM, AutoTokenizer  # lazy: heavy, optional

    src = local_path or repo
    kwargs: dict[str, Any] = {"local_files_only": True}
    if not local_path:
        kwargs["revision"] = revision
    tok = AutoTokenizer.from_pretrained(src, **kwargs)
    model = AutoModelForCausalLM.from_pretrained(src, **kwargs)

    def generate(raw_text: str) -> str:
        inputs = tok(raw_text, return_tensors="pt")
        out = model.generate(**inputs, max_new_tokens=512, do_sample=False)
        return tok.decode(out[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)

    return generate


class HFExtractor:
    def __init__(
        self,
        repo: str | None = None,
        revision: str | None = None,
        local_path: str | None = None,
        generate: Generate | None = None,
    ) -> None:
        self.repo = repo or os.environ.get("HF_REPO") or ""
        if not self.repo:
            raise ValueError("HF_REPO is not set")
        self.revision = _check_revision(revision or os.environ.get("HF_REVISION"))
        self.model_version = f"hf:{self.repo}@{self.revision}"
        self._generate = generate or _load_generate(
            self.repo, self.revision, local_path or os.environ.get("HF_LOCAL_PATH")
        )

    def extract(self, raw_text: str) -> Extraction:
        payload = _map_output(self._generate(raw_text))
        validate_extracted(payload)
        return Extraction(payload, self.model_version)
