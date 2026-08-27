"""Zero-execution, source-Eval-bound validator for M7A candidate bundles."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from agent_learning_loop.sft_exporter import (
    SftExportError,
    SftExportInfrastructureError,
    build_sft_candidate_artifacts,
)
from agent_learning_loop.sft_schemas import (
    SFT_ARTIFACT_PATHS,
    SftCandidateManifest,
    SftCandidateValidationResult,
    SftQualityReport,
    SftSample,
)


class SftCandidateValidationError(ValueError):
    """Candidate bytes are malformed or not derivable from the supplied source Eval."""


class SftCandidateInfrastructureError(RuntimeError):
    """A required directory or readable file was unavailable."""


def _directory_bytes(root: Path) -> dict[str, bytes]:
    try:
        if not root.exists():
            raise SftCandidateInfrastructureError("sft_candidate_root_missing")
        if root.is_symlink() or not root.is_dir():
            raise SftCandidateValidationError("sft_candidate_root_missing_or_symlink")
        resolved = root.resolve(strict=True)
        entries = list(root.iterdir())
        if any(not entry.is_file() or entry.is_symlink() for entry in entries):
            raise SftCandidateValidationError("sft_candidate_top_level_shape")
        if {entry.name for entry in entries} != set(SFT_ARTIFACT_PATHS):
            raise SftCandidateValidationError("sft_candidate_inventory")
        result: dict[str, bytes] = {}
        for path in sorted(entries, key=lambda item: item.name):
            path.resolve(strict=True).relative_to(resolved)
            data = path.read_bytes()
            if b"\r" in data:
                raise SftCandidateValidationError("sft_candidate_carriage_return")
            data.decode("utf-8")
            result[path.name] = data
        return result
    except SftCandidateValidationError:
        raise
    except SftCandidateInfrastructureError:
        raise
    except OSError as exc:
        raise SftCandidateInfrastructureError("unreadable_sft_candidate_directory") from exc
    except (UnicodeError, ValueError) as exc:
        raise SftCandidateValidationError("invalid_sft_candidate_directory") from exc


def _source_bytes(root: Path) -> dict[str, bytes]:
    try:
        if not root.exists():
            raise SftCandidateInfrastructureError("sft_source_root_missing")
        if root.is_symlink() or not root.is_dir():
            raise SftCandidateValidationError("sft_source_root_missing_or_symlink")
        resolved = root.resolve(strict=True)
        result: dict[str, bytes] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise SftCandidateValidationError("sft_source_contains_symlink")
            path.resolve(strict=True).relative_to(resolved)
            if path.is_file():
                result[path.relative_to(root).as_posix()] = path.read_bytes()
        return result
    except SftCandidateValidationError:
        raise
    except SftCandidateInfrastructureError:
        raise
    except OSError as exc:
        raise SftCandidateInfrastructureError("unreadable_sft_source_directory") from exc
    except ValueError as exc:
        raise SftCandidateValidationError("invalid_sft_source_directory") from exc


def validate_sft_candidates(
    bundle: Path,
    eval_bundle: Path,
) -> SftCandidateValidationResult:
    """Rebuild expected bytes from source evidence and compare without execution."""
    try:
        dataset_before = _directory_bytes(bundle)
        source_before = _source_bytes(eval_bundle)

        manifest = SftCandidateManifest.model_validate_json(
            dataset_before["dataset-manifest.json"]
        )
        quality = SftQualityReport.model_validate_json(
            dataset_before["quality-report.json"]
        )
        sample_lines = dataset_before["samples.jsonl"].decode("utf-8").splitlines()
        if len(sample_lines) != 18:
            raise SftCandidateValidationError("sft_candidate_sample_count")
        samples = [SftSample.model_validate_json(line) for line in sample_lines]
        if manifest.sample_ids != [sample.sample_id for sample in samples]:
            raise SftCandidateValidationError("sft_candidate_sample_identity_order")
        if manifest.sample_fingerprints != [
            sample.sample_fingerprint for sample in samples
        ]:
            raise SftCandidateValidationError("sft_candidate_sample_fingerprint_order")
        if quality.eligible_samples != manifest.sample_count:
            raise SftCandidateValidationError("sft_candidate_quality_manifest_count")
        dataset_before["report.md"].decode("utf-8")

        expected = build_sft_candidate_artifacts(eval_bundle)
        if dataset_before != expected.files:
            raise SftCandidateValidationError("sft_candidate_not_source_derived")
        if manifest != expected.manifest or quality != expected.quality:
            raise SftCandidateValidationError("sft_candidate_model_identity")

        dataset_after = _directory_bytes(bundle)
        source_after = _source_bytes(eval_bundle)
        if dataset_before != dataset_after:
            raise SftCandidateValidationError("sft_validator_changed_dataset_bytes")
        if source_before != source_after:
            raise SftCandidateValidationError("sft_validator_changed_source_bytes")
        return SftCandidateValidationResult(source_commit=manifest.source_commit)
    except SftCandidateValidationError:
        raise
    except SftCandidateInfrastructureError:
        raise
    except SftExportInfrastructureError as exc:
        raise SftCandidateInfrastructureError("sft_candidate_source_unavailable") from exc
    except SftExportError as exc:
        raise SftCandidateValidationError("sft_candidate_source_invalid") from exc
    except OSError as exc:
        raise SftCandidateInfrastructureError("sft_candidate_io_failure") from exc
    except (KeyError, UnicodeError, ValidationError, ValueError) as exc:
        raise SftCandidateValidationError("invalid_sft_candidate") from exc
