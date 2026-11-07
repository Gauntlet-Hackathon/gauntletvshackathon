"""HFExtractor: the real SLM adapter (F25a).

The model is an encoder with classification heads and BIO span tags (H11, docs/DECISIONS.md
"Model output format (H1a)"). The trainer's inference code is vendored in `rescue_field.tagger`;
this module resolves the pinned checkpoint folder (no network at runtime) and wraps `infer.Extractor`.
torch is imported lazily, only when the real model is loaded.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from rescue_field.slm import Extraction, validate_extracted

_SHA = re.compile(r"^[0-9a-f]{40}$")
ExtractorFactory = Callable[[str], Any]  # folder -> object with .extract(text) -> dict


def _check_revision(revision: str | None) -> str:
    if not revision or not _SHA.match(revision):
        raise ValueError("HF_REVISION must be a full 40-char commit SHA (never a branch like main)")
    return revision


def _snapshot_download(repo: str, revision: str) -> str:
    """Local-cache-only snapshot lookup. Module-level so tests can monkeypatch it."""
    from huggingface_hub import snapshot_download  # lazy: not installed in CI

    return snapshot_download(repo, revision=revision, local_files_only=True)


def _resolve_folder(repo: str, revision: str, local_path: str | None) -> str:
    folder = local_path or os.environ.get("HF_MODEL_DIR")
    return folder or _snapshot_download(repo, revision)


def _load_vendored(folder: str, model_version: str) -> Any:
    from rescue_field.tagger.infer import Extractor  # lazy: imports torch

    return Extractor.load(folder, model_version, threads=4)


class HFExtractor:
    def __init__(
        self,
        repo: str | None = None,
        revision: str | None = None,
        local_path: str | None = None,
        extractor_factory: ExtractorFactory | None = None,
    ) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        self.repo = repo or os.environ.get("HF_REPO") or ""
        if not self.repo:
            raise ValueError("HF_REPO is not set")
        self.revision = _check_revision(revision or os.environ.get("HF_REVISION"))
        self.model_version = f"hf:{self.repo}@{self.revision}"
        folder = _resolve_folder(self.repo, self.revision, local_path)
        self._extractor = (
            extractor_factory(folder)
            if extractor_factory
            else _load_vendored(folder, self.model_version)
        )

    def extract(self, raw_text: str) -> Extraction:
        payload = self._extractor.extract(raw_text)
        validate_extracted(payload)
        return Extraction(payload, self.model_version)
