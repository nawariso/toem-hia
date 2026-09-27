"""Pinned pretrained-model artifacts (Requirement 004 Gate A: exact model bytes).

A family B/C configuration carries a ``model_artifact`` pin:

* ``repo_id`` and an immutable 40-hex commit ``revision`` (branch or tag names
  are refused because they can move);
* the SHA-256 of every file used (``config_file`` and ``weights_file``).

``fetch-model`` downloads exactly that revision into a cache *outside Git*.
Evaluation calls :func:`acquire` with ``allow_download=False``: it only resolves
already-cached files and re-hashes them. Any mismatch raises
:class:`ModelArtifactError`, so an experiment can never silently run on
different weights. The experiment record stores :meth:`ResolvedArtifact.record`.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from toem_reid.hashing import sha256_file

SOURCES: Final = ("huggingface",)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelArtifactError(RuntimeError):
    pass


class Downloader(Protocol):
    def __call__(
        self,
        *,
        repo_id: str,
        filename: str,
        revision: str,
        cache_dir: str,
        local_files_only: bool,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ModelPin:
    source: str
    repo_id: str
    revision: str
    config_file: str
    weights_file: str
    files: dict[str, str]
    license: str
    licence_scope: str


@dataclass(frozen=True, slots=True)
class ResolvedArtifact:
    pin: ModelPin
    paths: dict[str, Path]
    files: dict[str, dict[str, Any]]

    @property
    def revision(self) -> str:
        return self.pin.revision

    @property
    def config_path(self) -> Path:
        return self.paths[self.pin.config_file]

    @property
    def weights_path(self) -> Path:
        return self.paths[self.pin.weights_file]

    def record(self) -> dict[str, Any]:
        """Provenance for the experiment record; never includes local cache paths."""
        return {
            "source": self.pin.source,
            "repo_id": self.pin.repo_id,
            "revision": self.pin.revision,
            "weights_file": self.pin.weights_file,
            "weights_sha256": self.files[self.pin.weights_file]["sha256"],
            "files": self.files,
            "license": self.pin.license,
            "licence_scope": self.pin.licence_scope,
            "verified": True,
        }


def parse_pin(data: Mapping[str, Any]) -> ModelPin:
    errors: list[str] = []
    if data.get("source") not in SOURCES:
        errors.append(f"model_artifact.source must be one of {list(SOURCES)}")
    for name in ("repo_id", "config_file", "weights_file", "license", "licence_scope"):
        if not isinstance(data.get(name), str) or not data[name].strip():
            errors.append(f"model_artifact.{name} is required")
    revision = data.get("revision")
    if not isinstance(revision, str) or not _COMMIT.match(revision):
        errors.append("model_artifact.revision must be an immutable 40-hex commit SHA")
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        errors.append("model_artifact.files must map each file to its sha256")
        files = {}
    for name, digest in files.items():
        if not isinstance(digest, str) or not _SHA256.match(digest):
            errors.append(f"model_artifact.files[{name!r}] must be a lowercase hex sha256")
    for key in ("config_file", "weights_file"):
        if isinstance(data.get(key), str) and data[key] not in files:
            errors.append(f"model_artifact.{key} {data[key]!r} is not listed in files")
    if errors:
        raise ValueError("; ".join(errors))
    return ModelPin(
        source=data["source"],
        repo_id=data["repo_id"],
        revision=data["revision"],
        config_file=data["config_file"],
        weights_file=data["weights_file"],
        files=dict(files),
        license=data["license"],
        licence_scope=data["licence_scope"],
    )


def _hub_download() -> Downloader:  # pragma: no cover - needs the embedding group
    # Imported dynamically (like timm/torch in matchers) so the base environment,
    # and strict mypy in it, never depends on the optional embedding group.
    try:
        hub = importlib.import_module("huggingface_hub")
    except ModuleNotFoundError:
        raise ModelArtifactError(
            "model acquisition needs the optional 'embedding' dependency group: "
            "uv sync --group embedding"
        ) from None
    download: Downloader = hub.hf_hub_download
    return download


def acquire(
    pin: ModelPin,
    cache_dir: Path,
    *,
    allow_download: bool,
    downloader: Downloader | Callable[..., str] | None = None,
) -> ResolvedArtifact:
    """Resolve every pinned file at the pinned revision and verify its SHA-256."""
    download = downloader or _hub_download()
    paths: dict[str, Path] = {}
    files: dict[str, dict[str, Any]] = {}
    for filename in sorted(pin.files):
        try:
            local = download(
                repo_id=pin.repo_id,
                filename=filename,
                revision=pin.revision,
                cache_dir=os.fspath(cache_dir),
                local_files_only=not allow_download,
            )
        except Exception as error:
            if allow_download:
                raise ModelArtifactError(
                    f"could not download {pin.repo_id}@{pin.revision}/{filename}: {error}"
                ) from error
            raise ModelArtifactError(
                f"{pin.repo_id}@{pin.revision}/{filename} is not in the model cache "
                f"{cache_dir}; run `toem-reid fetch-model` first (evaluation never downloads)"
            ) from error
        path = Path(local)
        actual = sha256_file(path)
        if actual != pin.files[filename]:
            raise ModelArtifactError(
                f"SHA-256 mismatch for {filename} at {pin.repo_id}@{pin.revision}: "
                f"expected {pin.files[filename]}, got {actual}; refusing to use different weights"
            )
        paths[filename] = path
        files[filename] = {"sha256": actual, "bytes": path.stat().st_size}
    return ResolvedArtifact(pin, paths, files)


def read_model_config(config_path: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Architecture, canonical ``pretrained_cfg`` and ``model_args`` from a verified config.json.

    This mirrors how timm itself builds a hub model from ``config.json``; reading
    it from the hash-verified file keeps preprocessing tied to the pinned model.
    """
    data = json.loads(config_path.read_text(encoding="utf-8"))
    architecture = data.get("architecture")
    cfg = data.get("pretrained_cfg")
    model_args = data.get("model_args", {})
    if not isinstance(architecture, str) or not isinstance(cfg, dict):
        raise ModelArtifactError(f"{config_path.name} lacks architecture/pretrained_cfg")
    if not isinstance(model_args, dict):
        raise ModelArtifactError(f"{config_path.name} has a non-object model_args")
    return architecture, cfg, model_args
