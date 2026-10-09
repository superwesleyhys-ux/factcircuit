"""Leakage-gated paired comparison on propositions made during 2023.

``run`` deliberately has no gold argument and reads only the frozen inference
inputs and source manifest.  ``audit`` and ``score`` are the only commands that
open the separately frozen gold file.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
import sys
sys.path.insert(0, str(HERE))

import loop_compare
from model_io import digest, write


SCHEMA_VERSION = 1
BENCHMARK_CUTOFF = "2024-12-31T23:59:59Z"
CLAIM_WINDOW = {
    "start": "2023-01-01T00:00:00Z",
    "end": "2023-12-31T23:59:59Z",
}
EXECUTION_PROFILE = "equal-v1"
ARM_ORDER = (("monolithic", "monolithic"), ("staged", "staged"))
_HEX256 = re.compile(r"[0-9a-f]{64}")
_FOUR_DIGIT_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_VERSION_PROOF_KINDS = {
    "sec_edgar_accession",
    "nasa_ntrs_citation",
    "timestamped_web_archive",
    "dated_official_pdf",
    "official_repository_record",
}
_ANSWER_KEYS = {
    "answer", "decision", "expected", "expected_decision", "gold", "label",
    "truth", "ground_truth", "verdict", "adjudication_source_version_ids",
}


def _reject_json_constant(value):
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def _loads(value):
    return json.loads(value, parse_constant=_reject_json_constant)


def _json(path):
    return _loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _content_sha(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _timestamp(value, name):
    if type(value) is not str:
        raise ValueError(f"{name} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing timezone")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a timezone-aware ISO timestamp") from exc


def _nonempty(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _exact_fields(value, fields, name):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError(f"{name} has unexpected or missing fields")


def _reject_answers(value, name="inference data"):
    """Keep labels and adjudication pointers outside every inference object."""
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is str and key.lower() in _ANSWER_KEYS:
                raise ValueError(f"{name} contains forbidden answer field: {key}")
            _reject_answers(child, name)
    elif type(value) is list:
        for child in value:
            _reject_answers(child, name)


def _reject_post_cutoff_year_text(value, path="inputs"):
    """Reject explicit 2025+ years in every model-visible input string.

    ``retrieved_at`` is intentionally audit-only: both semantic adapters omit
    it from their serialized payloads, so its honest 2026 capture time is not
    a hindsight leak.  All other strings in the strict input contract are
    visible to at least one model stage and are scanned recursively.
    """
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is str and key.lower() == "retrieved_at":
                continue
            _reject_post_cutoff_year_text(child, f"{path}.{key}")
    elif type(value) is list:
        for index, child in enumerate(value):
            _reject_post_cutoff_year_text(child, f"{path}[{index}]")
    elif type(value) is str:
        leaked = [match.group(1) for match in _FOUR_DIGIT_YEAR.finditer(value)
                  if int(match.group(1)) >= 2025]
        if leaked:
            raise ValueError(
                f"Model-visible input contains post-2024 year {leaked[0]} at {path}")


def _load_freeze(path):
    value = _json(path)
    _exact_fields(value, {"schema_version", "benchmark_id",
                          "pre_run_checksum", "files", "rule"},
                  "freeze manifest")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported freeze schema_version")
    _nonempty(value["benchmark_id"], "freeze benchmark_id")
    _nonempty(value["rule"], "freeze rule")
    if value["pre_run_checksum"] is not True:
        raise ValueError("Freeze manifest must declare pre-run checksums")
    if type(value["files"]) is not dict or not value["files"]:
        raise ValueError("freeze files must be a nonempty object")
    for name, file_digest in value["files"].items():
        _nonempty(name, "freeze filename")
        if type(file_digest) is not str or not _HEX256.fullmatch(file_digest):
            raise ValueError("freeze file digest must be lowercase sha256")
    return value


def _frozen_digest(files, path):
    """Resolve exact or directory-qualified names without guessing ambiguity."""
    path = Path(path)
    candidates = [value for name, value in files.items()
                  if name == str(path) or Path(name).name == path.name]
    if len(candidates) != 1:
        raise ValueError(f"freeze manifest must identify {path.name} exactly once")
    return candidates[0]


def _check_frozen(freeze, paths):
    for path in paths:
        if _frozen_digest(freeze["files"], path) != _sha(path):
            raise ValueError(f"Frozen sha256 mismatch for {Path(path).name}")


def load_sources(path):
    data = _json(path)
    _exact_fields(data, {"schema_version", "benchmark_id", "task",
                         "claim_window", "evidence_cutoff", "capture", "cases"},
                  "sources manifest")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported sources schema_version")
    _nonempty(data["benchmark_id"], "sources benchmark_id")
    _nonempty(data["task"], "sources task")
    if data["claim_window"] != CLAIM_WINDOW:
        raise ValueError("claim_window must cover exactly calendar year 2023 UTC")
    if data["evidence_cutoff"] != BENCHMARK_CUTOFF:
        raise ValueError("evidence_cutoff must be 2024-12-31T23:59:59Z")
    if type(data["capture"]) is not dict or not data["capture"]:
        raise ValueError("capture must document the snapshot method")
    if type(data["cases"]) is not list or not data["cases"]:
        raise ValueError("sources cases must be a nonempty array")
    _reject_answers(data, "sources manifest")

    seen = set()
    for index, case in enumerate(data["cases"]):
        prefix = f"sources case {index}"
        _exact_fields(case, {"id", "event_id", "domain", "modality",
                             "claim_made_at", "resolution_deadline",
                             "normalization", "materials"},
                      prefix)
        for field in ("id", "event_id", "domain", "modality"):
            _nonempty(case[field], f"{prefix}.{field}")
        if case["id"] in seen:
            raise ValueError("Duplicate sources case id")
        seen.add(case["id"])
        claim_time = _timestamp(case["claim_made_at"],
                                f"{prefix}.claim_made_at")
        if not (_timestamp(CLAIM_WINDOW["start"], "claim window start")
                <= claim_time
                <= _timestamp(CLAIM_WINDOW["end"], "claim window end")):
            raise ValueError("Every claim_made_at must fall in 2023 UTC")
        deadline = _timestamp(case["resolution_deadline"],
                              f"{prefix}.resolution_deadline")
        if deadline > _timestamp(BENCHMARK_CUTOFF, "benchmark cutoff"):
            raise ValueError("resolution_deadline cannot exceed the benchmark cutoff")
        if not ((type(case["normalization"]) is str
                 and case["normalization"].strip())
                or (type(case["normalization"]) is dict
                    and case["normalization"])):
            raise ValueError("normalization must be a nonempty string or object")
        if type(case["materials"]) is not list or not case["materials"]:
            raise ValueError("Each sources case needs material metadata")
        versions = set()
        for material in case["materials"]:
            _exact_fields(material, {"version_id", "role", "source_url",
                                     "content_sha256", "source_format",
                                     "official_source", "body_only",
                                     "version_proof"},
                          f"{prefix} material")
            _nonempty(material["version_id"], "source material version_id")
            if material["version_id"] in versions:
                raise ValueError("Duplicate source material version_id in case")
            versions.add(material["version_id"])
            if material["role"] not in {"claim", "outcome"}:
                raise ValueError("material role must be claim or outcome")
            _nonempty(material["source_url"], "source_url")
            parsed_url = urlsplit(material["source_url"])
            if parsed_url.scheme != "https" or not parsed_url.netloc:
                raise ValueError("source_url must be an absolute HTTPS URL")
            if (type(material["content_sha256"]) is not str
                    or not _HEX256.fullmatch(material["content_sha256"])):
                raise ValueError("content_sha256 must be lowercase sha256")
            _nonempty(material["source_format"], "source_format")
            if material["official_source"] is not True:
                raise ValueError("Historical materials must be official sources")
            if material["body_only"] is not True:
                raise ValueError("Historical captures must contain body-only text")
            proof = material["version_proof"]
            _exact_fields(proof, {"kind", "locator", "observed_at",
                                  "identifier", "artifact_sha256",
                                  "artifact_bytes"}, "version_proof")
            for field in ("kind", "locator", "identifier"):
                _nonempty(proof[field], f"version_proof.{field}")
            if proof["kind"] not in _VERSION_PROOF_KINDS:
                raise ValueError("version_proof kind is unsupported")
            proof_locator = urlsplit(proof["locator"])
            if (proof_locator.scheme != "https"
                    or not proof_locator.netloc):
                raise ValueError(
                    "version_proof locator must be an absolute HTTPS URL")
            observed = _timestamp(proof["observed_at"],
                                  "version_proof.observed_at")
            if observed > _timestamp(BENCHMARK_CUTOFF, "benchmark cutoff"):
                raise ValueError("version proof cannot be observed after the cutoff")
            if (type(proof["artifact_sha256"]) is not str
                    or not _HEX256.fullmatch(proof["artifact_sha256"])):
                raise ValueError("version_proof artifact_sha256 must be lowercase sha256")
            if (type(proof["artifact_bytes"]) is not int
                    or proof["artifact_bytes"] <= 0):
                raise ValueError("version_proof artifact_bytes must be positive")
    return data


def load_gold(path):
    data = _json(path)
    _exact_fields(data, {"schema_version", "dataset_kind", "cases",
                         "limitations"}, "gold")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported gold schema_version")
    _nonempty(data["dataset_kind"], "gold dataset_kind")
    if not ((type(data["limitations"]) is str and data["limitations"].strip())
            or (type(data["limitations"]) is list
                and data["limitations"]
                and all(type(item) is str and item.strip()
                        for item in data["limitations"]))):
        raise ValueError("gold limitations must be nonempty")
    if type(data["cases"]) is not list or not data["cases"]:
        raise ValueError("gold cases must be a nonempty array")
    seen = set()
    for case in data["cases"]:
        _exact_fields(case, {"id", "event_id", "assessment_mode", "decision",
                             "adjudication_source_version_ids", "rationale",
                             "basis"}, "gold case")
        for field in ("id", "event_id"):
            _nonempty(case[field], f"gold.{field}")
        if case["id"] in seen:
            raise ValueError("Duplicate gold case id")
        seen.add(case["id"])
        if case["assessment_mode"] != "evidence":
            raise ValueError("Historical gold must use evidence assessment_mode")
        if case["decision"] not in {"true", "false"}:
            raise ValueError("Historical gold decisions must be true or false")
        _nonempty(case["rationale"], "gold rationale")
        ids = case["adjudication_source_version_ids"]
        if (type(ids) is not list or not ids
                or len(ids) != len(set(ids))
                or any(type(item) is not str or not item for item in ids)):
            raise ValueError("Gold adjudication source ids must be unique and nonempty")
        if type(case["basis"]) is not list or not case["basis"]:
            raise ValueError("Gold basis must be a nonempty array")
        seen_basis = set()
        for item in case["basis"]:
            _exact_fields(item, {"version_id", "quote"}, "gold basis")
            _nonempty(item["version_id"], "gold basis version_id")
            _nonempty(item["quote"], "gold basis quote")
            key = (item["version_id"], item["quote"])
            if key in seen_basis:
                raise ValueError("Duplicate gold basis")
            seen_basis.add(key)
    return data


def validate_inference(inputs_path, sources_path, freeze_path):
    """Validate only files legally available during inference."""
    # loop_compare owns the structural codec; pre-parse here to enforce this
    # benchmark's stricter RFC-JSON rule (no NaN or infinities).
    _json(inputs_path)
    inputs = loop_compare.load_inputs(inputs_path)
    sources = load_sources(sources_path)
    freeze = _load_freeze(freeze_path)
    _check_frozen(freeze, [inputs_path, sources_path])
    if freeze["benchmark_id"] != sources["benchmark_id"]:
        raise ValueError("Freeze and sources benchmark_id mismatch")
    _reject_answers(inputs, "inputs")
    _reject_post_cutoff_year_text(inputs)

    source_cases = {case["id"]: case for case in sources["cases"]}
    input_cases = {case["target"]["id"]: case for case in inputs["cases"]}
    if input_cases.keys() != source_cases.keys():
        raise ValueError("Inputs and sources must contain the same cases")
    cutoff = _timestamp(BENCHMARK_CUTOFF, "benchmark cutoff")
    for identifier, case in input_cases.items():
        target = case["target"]
        source_case = source_cases[identifier]
        if target["as_of"] != BENCHMARK_CUTOFF:
            raise ValueError("Every target as_of must equal the benchmark cutoff")
        if target["assessment_mode"] != "evidence":
            raise ValueError("Historical targets must use evidence assessment_mode")
        materials = {item["version_id"]: item for item in case["materials"]}
        metadata = {item["version_id"]: item
                    for item in source_case["materials"]}
        if materials.keys() != metadata.keys():
            raise ValueError("Input materials and source metadata must match exactly")
        claim_ids = [item["version_id"] for item in source_case["materials"]
                     if item["role"] == "claim"]
        outcome_ids = [item["version_id"] for item in source_case["materials"]
                       if item["role"] == "outcome"]
        if len(claim_ids) != 1 or not outcome_ids:
            raise ValueError("Each case needs exactly one claim and at least one outcome")
        if target["source_version_id"] != claim_ids[0]:
            raise ValueError("source_version_id must identify the 2023 claim")
        if case["seed_ids"] != claim_ids:
            raise ValueError("seed_ids must contain only the single 2023 claim")
        if (len(target["evidence_scope"]) != len(set(target["evidence_scope"]))
                or set(target["evidence_scope"]) != set(outcome_ids)):
            raise ValueError("evidence_scope must contain every outcome and no claim")
        deadline = _timestamp(source_case["resolution_deadline"],
                              "resolution_deadline")
        material_times = {}
        for version_id, material in materials.items():
            meta = metadata[version_id]
            if meta["source_url"] != material["url"]:
                raise ValueError(f"source_url mismatch for {version_id}")
            if _content_sha(material["content"]) != meta["content_sha256"]:
                raise ValueError(f"Content sha256 mismatch for {version_id}")
            published = _timestamp(material["published_at"],
                                   f"{version_id}.published_at")
            available = _timestamp(material["available_at"],
                                   f"{version_id}.available_at")
            _timestamp(material["retrieved_at"], f"{version_id}.retrieved_at")
            if meta["version_proof"]["observed_at"] != material["available_at"]:
                raise ValueError(
                    f"version_proof observed_at must equal available_at for {version_id}")
            if published > cutoff or available > cutoff:
                raise ValueError("No material published or available after 2024 cutoff")
            material_times[version_id] = (published, available)
        claim_published, claim_available = material_times[claim_ids[0]]
        if claim_published.year != 2023:
            raise ValueError("Claim material must be published in 2023")
        for version_id in outcome_ids:
            outcome_published, outcome_available = material_times[version_id]
            if not claim_available < outcome_published:
                raise ValueError(
                    "Claim availability must strictly predate every outcome publication")
            if (outcome_published > deadline
                    or outcome_available > deadline):
                raise ValueError(
                    "Outcome publication and availability must not exceed resolution_deadline")
    return {"inputs": inputs, "sources": sources, "freeze": freeze,
            "input_sha256": _sha(inputs_path),
            "sources_sha256": _sha(sources_path),
            # This commits to the checksum manifest (including its gold
            # digest) without opening gold. The clean-commit gate below, not
            # this self-description, establishes that it predates API calls.
            "freeze_sha256": _sha(freeze_path)}


def validate_all(inputs_path, sources_path, gold_path, freeze_path):
    validated = validate_inference(inputs_path, sources_path, freeze_path)
    gold = load_gold(gold_path)
    _check_frozen(validated["freeze"], [inputs_path, sources_path, gold_path])
    sources = {case["id"]: case for case in validated["sources"]["cases"]}
    inputs = {case["target"]["id"]: case
              for case in validated["inputs"]["cases"]}
    gold_cases = {case["id"]: case for case in gold["cases"]}
    if sources.keys() != gold_cases.keys():
        raise ValueError("Gold must cover every frozen case exactly once")
    for identifier, expected in gold_cases.items():
        source_case = sources[identifier]
        outcomes = {item["version_id"] for item in source_case["materials"]
                    if item["role"] == "outcome"}
        if expected["event_id"] != source_case["event_id"]:
            raise ValueError("Gold and sources event_id mismatch")
        if set(expected["adjudication_source_version_ids"]) != outcomes:
            raise ValueError("Gold adjudication ids must equal frozen outcome sources")
        contents = {item["version_id"]: item["content"]
                    for item in inputs[identifier]["materials"]}
        basis_versions = set()
        for item in expected["basis"]:
            if item["version_id"] not in outcomes:
                raise ValueError("Gold basis must cite an outcome source")
            occurrences = contents[item["version_id"]].count(item["quote"])
            if occurrences != 1:
                raise ValueError("Gold basis quote must occur exactly once in its outcome")
            basis_versions.add(item["version_id"])
        if basis_versions != outcomes:
            raise ValueError("Gold basis must cover every adjudication outcome source")
    validated.update(gold=gold, gold_sha256=_sha(gold_path))
    return validated


def _git(arguments, root, text=True):
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True,
        text=text, timeout=15, check=False)
    if completed.returncode:
        raise RuntimeError("Git state could not be verified")
    return completed.stdout


def git_state(paths, repo_root=ROOT):
    """Bind an inference run to one clean commit and committed inputs."""
    discovered = Path(_git(["rev-parse", "--show-toplevel"], repo_root).strip())
    commit = _git(["rev-parse", "HEAD"], discovered).strip()
    _nonempty(commit, "git commit")
    dirty = bool(_git(["status", "--porcelain=v1", "--untracked-files=all"],
                      discovered).strip())
    files = {}
    all_committed = True
    for value in paths:
        path = Path(value).resolve()
        try:
            relative = path.relative_to(discovered.resolve())
        except ValueError:
            all_committed = False
            files[str(path)] = {"tracked_at_commit": False,
                                "current_sha256": _sha(path),
                                "commit_sha256": None}
            continue
        relative_text = relative.as_posix()
        tracked = subprocess.run(
            ["git", "-C", str(discovered), "cat-file", "-e",
             f"{commit}:{relative_text}"], capture_output=True,
            timeout=15, check=False).returncode == 0
        committed_hash = None
        if tracked:
            committed = _git(["show", f"{commit}:{relative_text}"],
                             discovered, text=False)
            committed_hash = hashlib.sha256(committed).hexdigest()
            tracked = committed_hash == _sha(path)
        all_committed = all_committed and tracked
        files[relative_text] = {
            "tracked_at_commit": tracked,
            "current_sha256": _sha(path),
            "commit_sha256": committed_hash,
        }
    return {
        "repository_root": str(discovered.resolve()),
        "commit": commit,
        "clean": not dirty,
        "all_inputs_at_commit": all_committed,
        "eligible": not dirty and all_committed,
        "files": files,
    }


def audit(args):
    validated = validate_all(args.inputs, args.sources, args.gold, args.freeze)
    gold = validated["gold"]
    result = {
        "status": "passed",
        "benchmark_id": validated["sources"]["benchmark_id"],
        "case_count": len(gold["cases"]),
        "claim_window": CLAIM_WINDOW,
        "evidence_cutoff": BENCHMARK_CUTOFF,
        "labels": {label: sum(case["decision"] == label
                              for case in gold["cases"])
                   for label in ("true", "false")},
        "checks": [
            "gold isolated from inference inputs and source metadata",
            "all claims made in 2023",
            "all declared evidence publication and availability timestamps fall on or before the 2024 cutoff",
            "all model-visible input text rejects explicit four-digit years from 2025 onward; audit-only retrieved_at is excluded",
            "declared body-only official-source captures match their internal content hashes",
            "seed/source/scope roles and pre-run checksums are internally consistent",
            "a clean committed worktree is separately required before model calls",
            "version-proof records are dataset declarations; this audit does not independently authenticate remote artifacts",
            "run call/output/trace checksums can establish internal consistency, not that an output came from the API",
        ],
    }
    if getattr(args, "output", None):
        write(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _blocked(out, reason):
    write(out / "status.json", {"status": reason, "model_calls": 0})
    return 2


def _raw_artifact_manifest(run_dir):
    run_dir = Path(run_dir)
    files = {}
    for arm_name, _ in ARM_ORDER:
        arm_dir = run_dir / arm_name
        if not arm_dir.is_dir():
            continue
        for path in sorted(arm_dir.rglob("*")):
            if path.is_symlink():
                raise ValueError("Raw artifacts cannot contain symbolic links")
            if path.is_file():
                files[str(path.relative_to(run_dir))] = _sha(path)
    return {"schema_version": SCHEMA_VERSION,
            "scope": "checksummed raw arm artifacts",
            "files": files}


def _write_raw_artifact_manifest(run_dir):
    manifest = _raw_artifact_manifest(run_dir)
    if not manifest["files"]:
        raise ValueError("No raw arm artifacts were produced")
    write(Path(run_dir) / "raw-artifacts.json", manifest)
    return manifest


def _verify_raw_artifact_manifest(run_dir):
    frozen = _json(Path(run_dir) / "raw-artifacts.json")
    _exact_fields(frozen, {"schema_version", "scope", "files"},
                  "raw artifact manifest")
    if (frozen["schema_version"] != SCHEMA_VERSION
            or frozen["scope"] != "checksummed raw arm artifacts"
            or type(frozen["files"]) is not dict
            or not frozen["files"]):
        raise ValueError("Invalid raw artifact manifest")
    current = _raw_artifact_manifest(run_dir)
    if current != frozen:
        raise ValueError("Raw artifact sha256 manifest mismatch")
    return frozen


def _normalized_arm(arm_name, arm_dir, input_cases, return_code=None,
                    arm_error=None):
    raw_path = arm_dir / "results.json"
    raw = _json(raw_path) if raw_path.exists() else []
    status_path = arm_dir / "status.json"
    raw_status = (_json(status_path) if status_path.exists() else {})
    loops = {}
    full_controls = {}
    for row in raw:
        if row.get("variant") == "full_evidence_once":
            if row.get("id") in full_controls:
                raise ValueError("Arm emitted duplicate full-evidence result")
            full_controls[row.get("id")] = row
            continue
        if row.get("variant") != "loop":
            raise ValueError("Arm emitted an unknown result variant")
        if row.get("id") in loops:
            raise ValueError("Arm emitted duplicate loop result")
        loops[row.get("id")] = row
    expected_ids = {case["target"]["id"] for case in input_cases}
    if not set(loops) <= expected_ids or not set(full_controls) <= expected_ids:
        raise ValueError("Arm emitted a result for an unknown case")

    def normalize(identifier, row):
        declared_completed = bool(row and row.get("status") == "completed")
        decision = None
        valid_prediction = False
        if declared_completed and type(row.get("prediction")) is dict:
            decision = row["prediction"].get("decision")
            valid_prediction = (
                decision in {"true", "false", "disputed", "unverifiable"}
                and row["prediction"].get("id") == identifier
                and row["prediction"].get("assessment_mode") == "evidence"
                and row["prediction"].get("as_of") == BENCHMARK_CUTOFF
                and row["prediction"].get("assessment_valid") is True)
        completed = declared_completed and valid_prediction
        if not valid_prediction:
            decision = "unverifiable"
        error_type = (row.get("error_type") if row else None)
        if declared_completed and not valid_prediction:
            error_type = "InvalidPrediction"
        if not completed and not error_type:
            error_type = arm_error or "MissingArmResult"
        return {
            "id": identifier,
            "status": "completed" if completed else "error",
            "decision": decision if completed else "unverifiable",
            "error_type": error_type,
            "usage": (row.get("usage") if row else None),
            "actual_new_api_usage": (row.get("actual_new_api_usage")
                                     if row else None),
        }

    cases = []
    for case in input_cases:
        identifier = case["target"]["id"]
        cases.append(normalize(identifier, loops.get(identifier)))
    controls = []
    for case in input_cases:
        if case["full_evidence_control"]:
            identifier = case["target"]["id"]
            controls.append(normalize(identifier,
                                      full_controls.get(identifier)))
    expected_controls = {case["target"]["id"] for case in input_cases
                         if case["full_evidence_control"]}
    if set(full_controls) != expected_controls:
        # Missing controls are already retained above as errors; extra controls
        # would have no frozen contract and therefore invalidate the arm.
        if not set(full_controls) <= expected_controls:
            raise ValueError("Arm emitted an unexpected full-evidence control")
    case_actual_calls = sum(
        ((case["actual_new_api_usage"] or {}).get("model_calls", 0))
        for case in cases + controls)
    arm_completed = (all(c["status"] == "completed"
                         for c in cases + controls)
                     and return_code in (None, 0)
                     and raw_status.get("status", "completed") == "completed")
    return {"name": arm_name,
            "status": "completed" if arm_completed else "has_errors",
            "return_code": return_code,
            "actual_model_calls": raw_status.get("actual_model_calls",
                                                  case_actual_calls),
            "logical_model_calls": raw_status.get("logical_model_calls"),
            "cases": cases,
            "full_evidence_controls": controls}


def run(args):
    """Run both arms without accepting or opening a gold file."""
    if not 1 <= args.max_rounds <= 10:
        raise ValueError("max_rounds must be 1..10")
    if getattr(args, "max_repairs", 1) not in (0, 1):
        raise ValueError("max_repairs must be 0 or 1")
    if getattr(args, "reuse_from", None) is not None:
        raise ValueError("Historical comparison forbids response replay")
    reasoning_effort = getattr(args, "reasoning_effort", None)
    if reasoning_effort is not None and (
            type(reasoning_effort) is not str or not reasoning_effort.strip()):
        raise ValueError("reasoning_effort must be a nonempty string or null")
    validated = validate_inference(args.inputs, args.sources, args.freeze)
    try:
        state = git_state([args.inputs, args.sources, args.freeze])
    except Exception as exc:
        state = {"eligible": False, "clean": False,
                 "all_inputs_at_commit": False,
                 "error_type": type(exc).__name__}

    contracts = {}
    for arm_name, semantic_mode in ARM_ORDER:
        contract_args = SimpleNamespace(
            max_rounds=args.max_rounds, execution_profile=EXECUTION_PROFILE)
        profile, budget, trace = loop_compare.execution_contract(
            contract_args, semantic_mode)
        contracts[arm_name] = {"profile": profile, "budget": asdict(budget),
                               "trace_config": asdict(trace)}
    if contracts["monolithic"] != contracts["staged"]:
        raise RuntimeError("equal-v1 did not produce identical arm contracts")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    code_sha256 = loop_compare.source_hashes()
    config = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": validated["sources"]["benchmark_id"],
        "model": args.model,
        "reasoning_effort": reasoning_effort,
        "execution_profile": EXECUTION_PROFILE,
        "budget": contracts["monolithic"]["budget"],
        "trace_config": contracts["monolithic"]["trace_config"],
        "budget_scope": (
            "Equal per-case/variant caps cover model calls, output tokens, "
            "wall time, and trace operations; input tokens are measured only."
        ),
        "model_retrieved_at_exposed": False,
        "arm_order": [name for name, _ in ARM_ORDER],
        "sequential_arms": True,
        "reuse_allowed": False,
        "max_inner_repairs": {"monolithic": 0,
                              "staged": getattr(args, "max_repairs", 1)},
        "input_sha256": validated["input_sha256"],
        "sources_sha256": validated["sources_sha256"],
        "freeze_sha256": validated["freeze_sha256"],
        "source_sha256": code_sha256,
        "git": state,
        "gold_access_during_inference": False,
        "knowledge_isolation_limit": (
            "Frozen evidence excludes post-cutoff sources; current model weights "
            "cannot cryptographically forget later training knowledge."
        ),
    }
    write(out / "config.json", config)
    if not state.get("eligible"):
        reason = ("blocked_dirty_git_worktree" if not state.get("clean")
                  else "blocked_inputs_not_at_git_commit")
        return _blocked(out, reason)
    if not os.environ.get("OPENAI_API_KEY"):
        return _blocked(out, "blocked_missing_auth")
    model_calls_authorized = (
        os.environ.get("FACTCIRCUIT_ALLOW_MODEL_CALLS") == "1"
        or os.environ.get("ACCURACY_TRACING_ALLOW_MODEL_CALLS") == "1"
    )
    if not model_calls_authorized:
        return _blocked(out, "blocked_model_calls_not_authorized")

    arm_results = []
    for arm_name, semantic_mode in ARM_ORDER:
        arm_dir = out / arm_name
        return_code = None
        arm_error = None
        try:
            return_code = loop_compare.run(SimpleNamespace(
                inputs=args.inputs,
                output=str(arm_dir),
                model=args.model,
                reasoning_effort=reasoning_effort,
                max_rounds=args.max_rounds,
                reuse_from=None,
                semantic_mode=semantic_mode,
                max_repairs=getattr(args, "max_repairs", 1),
                execution_profile=EXECUTION_PROFILE,
                source_manifest_sha256=validated["sources_sha256"],
                git_commit=state["commit"],
            ))
        except Exception as exc:
            arm_error = type(exc).__name__
            if not arm_dir.exists():
                arm_dir.mkdir(parents=True)
            write(arm_dir / "status.json", {
                "status": "runner_error", "error_type": arm_error})
        normalized = _normalized_arm(
            arm_name, arm_dir, validated["inputs"]["cases"],
            return_code=return_code, arm_error=arm_error)
        arm_results.append(normalized)
        write(out / "results.json", {
            "schema_version": SCHEMA_VERSION,
            "benchmark_id": validated["sources"]["benchmark_id"],
            "execution_profile": EXECUTION_PROFILE,
            "arms": arm_results,
        })

    has_errors = any(arm["status"] != "completed" for arm in arm_results)
    actual_calls = sum(arm["actual_model_calls"] for arm in arm_results)
    write(out / "status.json", {
        "status": "has_errors" if has_errors else "completed",
        "actual_model_calls": actual_calls,
        "errors_retained_as_unverifiable": True,
    })
    _write_raw_artifact_manifest(out)
    return 1 if has_errors else 0


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _arm_metrics(rows, gold):
    n = len(rows)
    true_ids = {identifier for identifier, case in gold.items()
                if case["decision"] == "true"}
    false_ids = set(gold) - true_ids
    correct = sum(row["decision"] == gold[row["id"]]["decision"]
                  for row in rows)
    true_correct = sum(row["id"] in true_ids and row["decision"] == "true"
                       for row in rows)
    false_correct = sum(row["id"] in false_ids and row["decision"] == "false"
                        for row in rows)
    abstained = sum(row["decision"] == "unverifiable" for row in rows)
    completed = sum(row["status"] == "completed" for row in rows)
    return {
        "accuracy": {"value": _ratio(correct, n), "numerator": correct,
                     "denominator": n},
        "true_recall": {"value": _ratio(true_correct, len(true_ids)),
                        "numerator": true_correct,
                        "denominator": len(true_ids)},
        "false_recall": {"value": _ratio(false_correct, len(false_ids)),
                         "numerator": false_correct,
                         "denominator": len(false_ids)},
        "abstention_rate": {"value": _ratio(abstained, n),
                            "numerator": abstained, "denominator": n},
        "completion_rate": {"value": _ratio(completed, n),
                            "numerator": completed, "denominator": n},
    }


def _resource_totals(rows):
    return {
        "logical": {
            "model_calls": sum(row["usage"]["model_calls"] for row in rows),
            "input_tokens": sum(row["usage"]["input_tokens"] for row in rows),
            "output_tokens": sum(row["usage"]["output_tokens"] for row in rows),
            "wall_seconds_sum": sum(row["usage"]["seconds"] for row in rows),
        },
        "actual_new_api": {
            "model_calls": sum(row["actual_new_api_usage"]["model_calls"]
                               for row in rows),
            "input_tokens": sum(row["actual_new_api_usage"]["input_tokens"]
                                for row in rows),
            "output_tokens": sum(row["actual_new_api_usage"]["output_tokens"]
                                 for row in rows),
        },
    }


def _validated_usage(value, name, seconds=True):
    fields = {"model_calls", "input_tokens", "output_tokens"}
    if seconds:
        fields.add("seconds")
    _exact_fields(value, fields, name)
    for key in ("model_calls", "input_tokens", "output_tokens"):
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError(f"{name}.{key} must be a nonnegative integer")
    if seconds and (type(value["seconds"]) not in (int, float)
                    or (type(value["seconds"]) is float
                        and not math.isfinite(value["seconds"]))
                    or value["seconds"] < 0):
        raise ValueError(f"{name}.seconds must be nonnegative")
    return value


def _reject_model_retrieval_time(value):
    """Reject retrieval metadata anywhere in the actual serialized payload."""
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is str and key.lower() == "retrieved_at":
                raise ValueError(
                    "Historical model payload exposes forbidden retrieved_at")
            _reject_model_retrieval_time(child)
    elif type(value) is list:
        for child in value:
            _reject_model_retrieval_time(child)


def _validate_text_hash(value, digest_value, name):
    if type(value) is not str:
        raise ValueError(f"{name} must be text")
    if (type(digest_value) is not str
            or digest_value != hashlib.sha256(value.encode()).hexdigest()):
        raise ValueError(f"{name} sha256 mismatch")


def _validate_actual_model_sets(actual_models):
    """A paired score is meaningful only for one identical server model."""
    if any(len(actual_models.get(name, set())) != 1
           for name, _ in ARM_ORDER):
        raise ValueError(
            "Each paired arm must contain exactly one actual model")
    if actual_models["monolithic"] != actual_models["staged"]:
        raise ValueError("Paired arms did not use the same actual model")


def _validate_raw_rows(rows, input_cases):
    if type(rows) is not list:
        raise ValueError("Raw arm results must be an array")
    expected = {(case["target"]["id"], "loop") for case in input_cases}
    expected.update((case["target"]["id"], "full_evidence_once")
                    for case in input_cases if case["full_evidence_control"])
    seen = set()
    allowed = {"id", "variant", "status", "prediction", "checkpoints",
               "error_type", "usage", "actual_new_api_usage"}
    required = allowed - {"error_type"}
    for row in rows:
        if type(row) is not dict or not required <= set(row) <= allowed:
            raise ValueError("Raw result has unexpected or missing fields")
        key = (row["id"], row["variant"])
        if key in seen or key not in expected:
            raise ValueError("Raw result case/variant contract mismatch")
        seen.add(key)
        if row["status"] not in {"completed", "error"}:
            raise ValueError("Raw result status is invalid")
        if row["prediction"] is not None and type(row["prediction"]) is not dict:
            raise ValueError("Raw prediction must be an object or null")
        if type(row["checkpoints"]) is not list:
            raise ValueError("Raw checkpoints must be an array")
        for checkpoint in row["checkpoints"]:
            if (type(checkpoint) is not dict
                    or checkpoint.get("assessment_mode") != "evidence"):
                raise ValueError("Every raw checkpoint must use evidence mode")
        if "error_type" in row and row["error_type"] is not None and (
                type(row["error_type"]) is not str
                or not row["error_type"].strip()):
            raise ValueError("Raw error_type is invalid")
        if row["status"] == "error" and not row.get("error_type"):
            raise ValueError("Errored raw result requires error_type")
        if row["status"] == "completed" and row.get("error_type") is not None:
            raise ValueError("Completed raw result cannot declare error_type")
        _validated_usage(row["usage"], "raw usage")
        _validated_usage(row["actual_new_api_usage"],
                         "raw actual API usage", seconds=False)
    if seen != expected:
        raise ValueError("Raw arm results must retain every case and control")


def _validate_call_logs(arm_dir, rows, model, reasoning_effort, budget):
    expected_names = set()
    actual_models = set()
    response_ids = set()
    total_calls = 0
    for row in rows:
        suffix = "full" if row["variant"] == "full_evidence_once" else "loop"
        name = f"{row['id']}-{suffix}-calls.json"
        expected_names.add(name)
        records = _json(Path(arm_dir) / name)
        if type(records) is not list:
            raise ValueError("Call log must be an array")
        if len(records) > budget["calls"]:
            raise ValueError("Call log exceeds the per-case call cap")
        if [record.get("sequence") for record in records] != list(
                range(1, len(records) + 1)):
            raise ValueError("Call log sequences must be contiguous")
        input_tokens = output_tokens = successful_calls = 0
        call_seconds = 0.0
        for record in records:
            if type(record) is not dict:
                raise ValueError("Call log record must be an object")
            if "cached_from" in record or "cache_accounted" in record:
                raise ValueError("Historical comparison forbids replayed calls")
            request_fields = {
                "sequence", "system", "user", "request_digest",
                "system_prompt_sha256", "user_payload_sha256",
                "max_completion_tokens", "requested_model", "response_mode",
                "output_schema_sha256", "reasoning_effort",
            }
            response_fields = {
                "seconds", "response_id", "actual_model", "usage", "output",
                "output_sha256",
            }
            optional_fields = {"stage", "prompt_version", "error_type"}
            if not request_fields <= set(record) <= (
                    request_fields | response_fields | optional_fields):
                raise ValueError(
                    "Call log record has unexpected or missing request fields")
            if (record.get("requested_model") != model
                    or record.get("reasoning_effort") != reasoning_effort):
                raise ValueError("Call log model or reasoning mismatch")
            _validate_text_hash(record["system"],
                                record["system_prompt_sha256"],
                                "system prompt")
            _validate_text_hash(record["user"], record["user_payload_sha256"],
                                "user payload")
            if record["request_digest"] != digest(
                    [record["system"], record["user"]]):
                raise ValueError("Call request digest mismatch")
            try:
                user_payload = _loads(record["user"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Historical user payload must be valid JSON") from exc
            if type(user_payload) is not dict:
                raise ValueError("Historical user payload must be a JSON object")
            _reject_model_retrieval_time(user_payload)
            if record["response_mode"] not in {
                    "text", "json_object", "json_schema"}:
                raise ValueError("Call response_mode is invalid")
            schema_hash = record["output_schema_sha256"]
            if ((record["response_mode"] == "json_schema")
                    != (type(schema_hash) is str
                        and _HEX256.fullmatch(schema_hash) is not None)):
                raise ValueError("Call response schema declaration is invalid")
            if (record["response_mode"] != "json_schema"
                    and schema_hash is not None):
                raise ValueError(
                    "Non-schema call cannot declare an output schema")
            cap = record.get("max_completion_tokens")
            if (type(cap) is not int or cap < 1
                    or cap > budget["per_call_output_tokens"]):
                raise ValueError("Call record exceeds the per-call output cap")
            actual = record.get("actual_model")
            if actual is not None:
                _nonempty(actual, "actual_model")
                actual_models.add(actual)
            response_id = record.get("response_id")
            if response_id is not None:
                _nonempty(response_id, "response_id")
                if response_id in response_ids:
                    raise ValueError("Call response_id must be unique within an arm")
                response_ids.add(response_id)
            if (actual is None) != (response_id is None):
                raise ValueError(
                    "Call response_id and actual_model must appear together")
            seconds = record.get("seconds")
            if (seconds is not None
                    and (type(seconds) not in (int, float)
                         or (type(seconds) is float
                             and not math.isfinite(seconds))
                         or seconds < 0)):
                raise ValueError("Call seconds must be nonnegative")
            if seconds is not None:
                call_seconds += seconds
            usage = record.get("usage")
            if usage is not None:
                if actual is None:
                    raise ValueError("Call with usage is missing actual_model")
                _exact_fields(usage, {"input_tokens", "output_tokens"},
                              "call usage")
                if any(type(usage[key]) is not int or usage[key] < 0
                       for key in usage):
                    raise ValueError("Call usage must contain nonnegative integers")
                if usage["output_tokens"] > cap:
                    raise ValueError(
                        "Call usage exceeds its requested output-token cap")
                input_tokens += usage["input_tokens"]
                output_tokens += usage["output_tokens"]
            elif "error_type" not in record:
                raise ValueError("Successful call record is missing usage")
            output = record.get("output")
            output_hash = record.get("output_sha256")
            if (output is None) != (output_hash is None):
                raise ValueError("Call output and output_sha256 must appear together")
            if output is not None:
                _validate_text_hash(output, output_hash, "model output")
            if "error_type" in record:
                _nonempty(record["error_type"], "call error_type")
            else:
                if not response_fields <= set(record):
                    raise ValueError(
                        "Successful call record is missing response integrity fields")
                _nonempty(record["output"], "successful model output")
                if record["response_mode"] != "text":
                    try:
                        _loads(record["output"])
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            "Successful structured model output is not JSON") from exc
                successful_calls += 1
        if row["status"] == "completed" and successful_calls < 1:
            raise ValueError(
                "Completed raw result requires at least one successful model call")
        if row["usage"]["model_calls"] != len(records):
            raise ValueError("Raw result logical call count mismatch")
        if (row["usage"]["input_tokens"] != input_tokens
                or row["usage"]["output_tokens"] != output_tokens):
            raise ValueError("Raw result logical token usage mismatch")
        if row["actual_new_api_usage"] != {
                "model_calls": len(records), "input_tokens": input_tokens,
                "output_tokens": output_tokens}:
            raise ValueError("Raw result actual API usage mismatch")
        if output_tokens > budget["output_tokens"]:
            raise ValueError("Call log exceeds the per-case output-token cap")
        if row["usage"]["seconds"] > budget["seconds"]:
            raise ValueError("Raw result exceeds the per-case wall-time cap")
        if call_seconds > row["usage"]["seconds"] + 1e-9:
            raise ValueError(
                "Cumulative call seconds exceed raw result wall seconds")
        total_calls += len(records)
    found_names = {path.name for path in Path(arm_dir).glob("*-calls.json")}
    if found_names != expected_names:
        raise ValueError("Call log file set does not match raw cases")
    if len(actual_models) > 1:
        raise ValueError("One arm used more than one actual model")
    return actual_models, total_calls, response_ids


class _OfflineReplayClient:
    """Replay one case from logged outputs/errors with no network path."""

    def __init__(self, records, model, reasoning_effort, budget):
        self.pending = list(records)
        self.records = []
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.budget = budget
        self.used_output = 0

    def reserve(self, calls, output_tokens=None):
        required_output = (
            calls * self.budget["per_call_output_tokens"]
            if output_tokens is None else output_tokens)
        if type(calls) is not int or calls < 0:
            raise ValueError(
                "reservation calls must be a nonnegative integer")
        if type(required_output) is not int or required_output < 0:
            raise ValueError(
                "reservation output_tokens must be a nonnegative integer")
        if (self.budget["calls"] - len(self.records) < calls
                or self.budget["output_tokens"] - self.used_output
                < required_output):
            raise loop_compare.ResourceBudgetError(
                "Per-arm resource budget cannot cover staged transaction")

    def call(self, system, user, json_schema=None, json_mode=False, *,
             stage=None, prompt_version=None, max_output_tokens=None):
        response_mode = ("json_schema" if json_schema else
                         "json_object" if json_mode else "text")
        schema_hash = digest(json_schema) if json_schema else None
        requested_cap = (
            self.budget["per_call_output_tokens"]
            if max_output_tokens is None else max_output_tokens)
        if type(requested_cap) is not int or requested_cap < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        cap = min(requested_cap, self.budget["per_call_output_tokens"],
                  self.budget["output_tokens"] - self.used_output)
        if len(self.records) >= self.budget["calls"] or cap <= 0:
            raise loop_compare.ResourceBudgetError(
                "Per-arm resource budget exhausted")
        if not self.pending:
            raise ValueError("Offline replay requested an unlogged model call")
        record = self.pending.pop(0)
        expected = {
            "system": system,
            "user": user,
            "requested_model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "response_mode": response_mode,
            "output_schema_sha256": schema_hash,
            "max_completion_tokens": cap,
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise ValueError(
                "Offline replay request does not match its logged model call")
        if record.get("stage") != stage:
            raise ValueError("Offline replay stage does not match its call log")
        if record.get("prompt_version") != prompt_version:
            raise ValueError(
                "Offline replay prompt version does not match its call log")
        self.records.append(record)
        if "error_type" in record:
            # BudgetClient deliberately redacts remote error text but exposes
            # this deterministic local wrapper message to provenance.fail().
            # Recreate it exactly so a legitimate transport failure trace is
            # replayable rather than silently omitted from the score.
            raise RuntimeError(
                "Model call failed: " + record["error_type"])
        self.used_output += record["usage"]["output_tokens"]
        if response_mode == "text":
            return record["output"]
        return _loads(record["output"])


def _offline_replay_trace(case, variant, semantic_mode, trace_config,
                          max_repairs, records, model, reasoning_effort,
                          budget):
    client = _OfflineReplayClient(
        records, model, reasoning_effort, budget)
    materials = [loop_compare.p.MaterialVersion(**item)
                 for item in case["materials"]]
    seed_ids = ([item.version_id for item in materials]
                if variant == "full_evidence_once" else case["seed_ids"])
    provider = loop_compare.SnapshotSearchProvider(materials, seed_ids)
    target_value = {**case["target"],
                    "evidence_scope": tuple(
                        case["target"]["evidence_scope"])}
    target = loop_compare.p.Target(**target_value)
    if semantic_mode == "staged":
        decomposer = loop_compare.StagedDecomposer(
            client, max_repairs=max_repairs)
        verifier = loop_compare.StagedVerifier(
            client, max_repairs=max_repairs)
    else:
        decomposer = loop_compare.MonolithicDecomposer(client)
        verifier = loop_compare.MonolithicVerifier(client)
    config_value = dict(trace_config)
    if variant == "full_evidence_once":
        config_value["max_rounds"] = 1
    report = loop_compare.p.run_provenance(
        target, provider, decomposer, verifier,
        loop_compare.p.TraceConfig(**config_value))
    if client.pending:
        raise ValueError("Offline replay left unused logged model calls")
    report["model_call_lineage"] = loop_compare.model_call_lineage(records)
    if not report["assessment_valid"]:
        report["execution_failure"] = loop_compare.execution_failure_binding(
            report, records)
    return report


def _validate_missing_trace_failure(path, row, records, replayed):
    """Validate a pre-trace failure sidecar against deterministic replay."""
    failure = _json(path)
    _exact_fields(failure, {
        "schema_version", "status", "error_type",
        "call_artifact_sha256", "failed_call_sequence",
        "failed_call_error_type",
    }, "missing-trace failure")
    if (failure["schema_version"] != SCHEMA_VERSION
            or failure["status"] != "error"
            or failure["error_type"] != row.get("error_type")
            or failure["call_artifact_sha256"] != digest(records)):
        raise ValueError(
            "Missing-trace failure is not bound to its row and call log")
    sequence = failure["failed_call_sequence"]
    call_error = failure["failed_call_error_type"]
    if (sequence is None) != (call_error is None):
        raise ValueError("Missing-trace failed-call fields must appear together")
    if sequence is not None:
        if (type(sequence) is not int or sequence < 1
                or sequence > len(records)
                or records[sequence - 1].get("sequence") != sequence
                or records[sequence - 1].get("error_type") != call_error
                or call_error != failure["error_type"]):
            raise ValueError(
                "Missing-trace failure does not match its failed model call")
    if replayed.get("assessment_valid") is True:
        raise ValueError(
            "Successful offline replay cannot be marked as a missing-trace error")
    binding = replayed.get("execution_failure")
    if (type(binding) is not dict
            or binding.get("error_type") != failure["error_type"]
            or binding.get("call_artifact_sha256") != digest(records)
            or binding.get("failed_call_sequence") != sequence
            or binding.get("failed_call_error_type") != call_error):
        raise ValueError(
            "Missing-trace error is not reproduced by offline replay")


def _validate_trace_bindings(arm_dir, rows, input_cases, semantic_mode,
                             trace_config, max_repairs, model,
                             reasoning_effort, budget):
    """Replay outputs and recompute every trace, status, and presentation."""
    arm_dir = Path(arm_dir)
    cases = {case["target"]["id"]: case for case in input_cases}
    expected_failure_names = set()
    for row in rows:
        suffix = "full" if row["variant"] == "full_evidence_once" else "loop"
        trace_path = arm_dir / f"{row['id']}-{suffix}-trace.json"
        call_path = arm_dir / f"{row['id']}-{suffix}-calls.json"
        records = _json(call_path)
        try:
            replayed = _offline_replay_trace(
                cases[row["id"]], row["variant"], semantic_mode,
                trace_config, max_repairs, records, model,
                reasoning_effort, budget)
            # Compare the persisted JSON value, not Python tuple/list codec
            # details produced by dataclass reconstruction.
            replayed = json.loads(json.dumps(
                replayed, ensure_ascii=False, allow_nan=False))
        except Exception as exc:
            raise ValueError(
                "Raw trace cannot be reproduced from call outputs") from exc
        if not trace_path.is_file():
            failure_path = arm_dir / f"{row['id']}-{suffix}-failure.json"
            expected_failure_names.add(failure_path.name)
            if (row["status"] != "error" or row["prediction"] is not None
                    or row["checkpoints"] != []
                    or not failure_path.is_file()):
                raise ValueError(
                    "Raw result without a trace lacks a strict failure proof")
            _validate_missing_trace_failure(
                failure_path, row, records, replayed)
            continue
        trace = _json(trace_path)
        expected_lineage = loop_compare.model_call_lineage(records)
        if trace.get("model_call_lineage") != expected_lineage:
            raise ValueError(
                "Raw trace is not bound to its exact model call outputs")
        prediction = loop_compare.present_decision(replayed)
        checkpoints = loop_compare.round_decisions(replayed)
        if replayed != trace:
            raise ValueError(
                "Raw trace does not equal offline replay of model call outputs")
        replay_status = ("completed" if replayed.get("assessment_valid") is True
                         else "error")
        if row["status"] != replay_status:
            raise ValueError(
                "Raw result status does not match offline replay assessment status")
        if prediction != row["prediction"]:
            raise ValueError(
                "Raw prediction does not match its deterministic trace presentation")
        if checkpoints != row["checkpoints"]:
            raise ValueError(
                "Raw checkpoints do not match their deterministic trace presentation")
        if replay_status == "error":
            failure = replayed.get("execution_failure")
            if (type(failure) is not dict
                    or row.get("error_type") != failure.get("error_type")):
                raise ValueError(
                    "Raw error_type is not bound to replayed trace/call failure")
        elif row.get("error_type") is not None:
            raise ValueError("Successful replay cannot retain an error_type")
    found_failure_names = {
        path.name for path in arm_dir.glob("*-failure.json")}
    if found_failure_names != expected_failure_names:
        raise ValueError(
            "Failure sidecar file set does not match missing error traces")


def _validate_arm_artifacts(run_dir, arm_name, semantic_mode, outer,
                            input_cases, top_arm):
    arm_dir = Path(run_dir) / arm_name
    arm_config = _json(arm_dir / "config.json")
    expected_repairs = outer["max_inner_repairs"][arm_name]
    checks = {
        "model": outer["model"],
        "reasoning_effort": outer["reasoning_effort"],
        "execution_profile": EXECUTION_PROFILE,
        "trace_config": outer["trace_config"],
        "budget": outer["budget"],
        "semantic_mode": semantic_mode,
        "max_inner_repairs": expected_repairs,
        "input_sha256": outer["input_sha256"],
        "source_manifest_sha256": outer["sources_sha256"],
        "source_sha256": outer["source_sha256"],
        "git_commit": outer["git"]["commit"],
        "model_retrieved_at_exposed": False,
        "gold_access_during_inference": False,
        "reuse_from": None,
    }
    for key, expected in checks.items():
        if arm_config.get(key) != expected:
            raise ValueError(f"Raw {arm_name} config mismatch: {key}")
    if arm_config.get("input_tokens_capped") is not False:
        raise ValueError("Historical config must not claim an input-token cap")
    if ((semantic_mode == "staged")
            != (type(arm_config.get("prompt_manifest")) is dict)):
        raise ValueError("Arm prompt manifest does not match semantic mode")

    rows = _json(arm_dir / "results.json")
    _validate_raw_rows(rows, input_cases)
    actual_models, total_calls, response_ids = _validate_call_logs(
        arm_dir, rows, outer["model"], outer["reasoning_effort"],
        outer["budget"])
    _validate_trace_bindings(
        arm_dir, rows, input_cases, semantic_mode, outer["trace_config"],
        expected_repairs, outer["model"], outer["reasoning_effort"],
        outer["budget"])
    status = _json(arm_dir / "status.json")
    _exact_fields(status, {"status", "actual_model_calls",
                           "logical_model_calls", "replayed_model_calls"},
                  "raw arm status")
    if status["status"] not in {"completed", "has_errors"}:
        raise ValueError("Raw arm status is invalid")
    if (status["actual_model_calls"] != total_calls
            or status["logical_model_calls"] != total_calls
            or status["replayed_model_calls"] != 0):
        raise ValueError("Raw arm status call accounting mismatch")
    return_code = 0 if status["status"] == "completed" else 1
    normalized = _normalized_arm(
        arm_name, arm_dir, input_cases, return_code=return_code)
    if normalized != top_arm:
        raise ValueError("Top-level derived results do not match raw arm artifacts")
    return normalized, rows, actual_models, response_ids


def score(args):
    validated = validate_all(args.inputs, args.sources, args.gold, args.freeze)
    run_dir = Path(args.run)
    config = _json(run_dir / "config.json")
    if (config.get("benchmark_id") != validated["sources"]["benchmark_id"]
            or config.get("input_sha256") != validated["input_sha256"]
            or config.get("sources_sha256") != validated["sources_sha256"]
            or config.get("freeze_sha256") != validated["freeze_sha256"]):
        raise ValueError("Run does not match the frozen benchmark")
    if (config.get("execution_profile") != EXECUTION_PROFILE
            or config.get("gold_access_during_inference") is not False
            or config.get("sequential_arms") is not True
            or config.get("model_retrieved_at_exposed") is not False
            or config.get("reuse_allowed") is not False
            or config.get("source_sha256") != loop_compare.source_hashes()
            or not config.get("git", {}).get("eligible")
            or config.get("git", {}).get("clean") is not True
            or config.get("git", {}).get("all_inputs_at_commit") is not True
            or not config.get("git", {}).get("commit")):
        raise ValueError("Run did not use the historical comparison contract")
    trace_config = config.get("trace_config")
    if (type(trace_config) is not dict
            or type(trace_config.get("max_rounds")) is not int):
        raise ValueError("Run trace_config is invalid")
    _, expected_budget, expected_trace = loop_compare.execution_contract(
        SimpleNamespace(max_rounds=trace_config["max_rounds"],
                        execution_profile=EXECUTION_PROFILE), "monolithic")
    if (config.get("budget") != asdict(expected_budget)
            or trace_config != asdict(expected_trace)
            or config.get("max_inner_repairs", {}).get("monolithic") != 0
            or config.get("max_inner_repairs", {}).get("staged") not in (0, 1)):
        raise ValueError("Run did not use exact equal-v1 resource caps")
    _verify_raw_artifact_manifest(run_dir)
    results = _json(run_dir / "results.json")
    _exact_fields(results, {"schema_version", "benchmark_id",
                            "execution_profile", "arms"}, "run results")
    if (results["schema_version"] != SCHEMA_VERSION
            or results["benchmark_id"] != config["benchmark_id"]
            or results["execution_profile"] != EXECUTION_PROFILE):
        raise ValueError("Run result metadata mismatch")
    if ([arm.get("name") for arm in results["arms"]]
            != [name for name, _ in ARM_ORDER]):
        raise ValueError("Both paired arms must be present in sequential order")

    gold = {case["id"]: case for case in validated["gold"]["cases"]}
    by_arm = {}
    actual_models = {}
    response_ids = {}
    for arm, (_, semantic_mode) in zip(results["arms"], ARM_ORDER):
        normalized, _, model_set, arm_response_ids = _validate_arm_artifacts(
            run_dir, arm["name"], semantic_mode, config,
            validated["inputs"]["cases"], arm)
        actual_models[arm["name"]] = model_set
        response_ids[arm["name"]] = arm_response_ids
        arm = normalized
        rows = arm.get("cases")
        if (type(rows) is not list or len(rows) != len(gold)
                or {row.get("id") for row in rows} != set(gold)):
            raise ValueError("Every arm must retain every frozen case exactly once")
        for row in rows:
            if row.get("decision") not in {
                    "true", "false", "disputed", "unverifiable"}:
                raise ValueError("Invalid arm decision")
            if row.get("status") not in {"completed", "error"}:
                raise ValueError("Invalid arm status")
            if row["status"] == "error" and row["decision"] != "unverifiable":
                raise ValueError("Every errored case must be retained as unverifiable")
        by_arm[arm["name"]] = rows
    _validate_actual_model_sets(actual_models)
    if response_ids["monolithic"] & response_ids["staged"]:
        raise ValueError("Paired arms must not reuse any API response_id")

    arm_metrics = {name: _arm_metrics(by_arm[name], gold)
                   for name, _ in ARM_ORDER}
    paired = {}
    for metric in ("accuracy", "true_recall", "false_recall",
                   "abstention_rate", "completion_rate"):
        original = arm_metrics["monolithic"][metric]["value"]
        staged = arm_metrics["staged"][metric]["value"]
        paired[metric] = {
            "monolithic": original,
            "staged": staged,
            "staged_minus_monolithic": (staged - original
                                         if staged is not None
                                         and original is not None else None),
        }
    indexed = {name: {row["id"]: row for row in rows}
               for name, rows in by_arm.items()}
    top_arms = {arm["name"]: arm for arm in results["arms"]}
    indexed_controls = {
        name: {row["id"]: row
               for row in top_arms[name]["full_evidence_controls"]}
        for name, _ in ARM_ORDER}
    cases = []
    for identifier, expected in gold.items():
        original = indexed["monolithic"][identifier]
        staged = indexed["staged"][identifier]
        original_correct = original["decision"] == expected["decision"]
        staged_correct = staged["decision"] == expected["decision"]
        cases.append({
            "id": identifier,
            "event_id": expected["event_id"],
            "gold": expected["decision"],
            "gold_rationale": expected["rationale"],
            "gold_basis": expected["basis"],
            "monolithic": {"decision": original["decision"],
                           "status": original["status"],
                           "correct": original_correct,
                           "error_type": original.get("error_type"),
                           "usage": original["usage"],
                           "actual_new_api_usage": original["actual_new_api_usage"]},
            "staged": {"decision": staged["decision"],
                       "status": staged["status"],
                       "correct": staged_correct,
                       "error_type": staged.get("error_type"),
                       "usage": staged["usage"],
                       "actual_new_api_usage": staged["actual_new_api_usage"]},
            "paired_accuracy_change": (int(staged_correct)
                                       - int(original_correct)),
        })
    controls = []
    for case in validated["inputs"]["cases"]:
        if not case["full_evidence_control"]:
            continue
        identifier = case["target"]["id"]
        expected = gold[identifier]["decision"]
        row = {"id": identifier, "event_id": gold[identifier]["event_id"],
               "gold": expected,
               "gold_rationale": gold[identifier]["rationale"],
               "gold_basis": gold[identifier]["basis"], "arms": {}}
        for name, _ in ARM_ORDER:
            loop_row = indexed[name][identifier]
            control = indexed_controls[name][identifier]
            if control["status"] != "completed":
                diagnosis = "control_error"
            elif control["decision"] != expected:
                diagnosis = "semantic_failure_with_full_evidence"
            elif loop_row["decision"] != expected:
                diagnosis = "loop_or_evidence_access_failure"
            else:
                diagnosis = "loop_and_full_evidence_agree_with_gold"
            row["arms"][name] = {
                "loop_decision": loop_row["decision"],
                "control_decision": control["decision"],
                "control_status": control["status"],
                "control_error_type": control.get("error_type"),
                "diagnosis": diagnosis,
                "usage": control["usage"],
                "actual_new_api_usage": control["actual_new_api_usage"],
            }
        controls.append(row)
    resources = {}
    for name, _ in ARM_ORDER:
        main_rows = top_arms[name]["cases"]
        control_rows = top_arms[name]["full_evidence_controls"]
        resources[name] = {
            "main_cases": _resource_totals(main_rows),
            "full_evidence_controls": _resource_totals(control_rows),
            "all_variants": _resource_totals(main_rows + control_rows),
        }
    output = {
        "schema_version": SCHEMA_VERSION,
        "status": "scored",
        "benchmark_id": config["benchmark_id"],
        "case_count": len(cases),
        "metrics_by_arm": arm_metrics,
        "paired_metrics": paired,
        "cases": cases,
        "full_evidence_control_diagnostics": controls,
        "resource_usage_by_arm": resources,
        "actual_models": {name: sorted(actual_models[name])
                          for name, _ in ARM_ORDER},
        "limits": [config["knowledge_isolation_limit"],
                   "Small benchmark results are descriptive, not a population claim.",
                   "Input tokens are measured but are not a budget cap.",
                   ("Version-proof metadata is a recorded dataset declaration "
                    "with internal hash consistency; this local audit does not "
                    "independently authenticate remote source artifacts."),
                   ("Call/output/trace hashes establish internal consistency "
                    "only; they cannot authenticate that a logged output was "
                    "actually returned by the model API.")],
    }
    destination = (Path(args.output) if getattr(args, "output", None)
                   else run_dir / "scores.json")
    write(destination, output)
    return 0


def _data_args(parser, gold=False):
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--sources", required=True)
    parser.add_argument("--freeze", required=True)
    if gold:
        parser.add_argument("--gold", required=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit")
    _data_args(a, gold=True)
    a.add_argument("--output")
    r = sub.add_parser("run")
    _data_args(r, gold=False)
    r.add_argument("--output", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--reasoning-effort")
    r.add_argument("--max-rounds", type=int, default=5)
    r.add_argument("--max-repairs", type=int, choices=[0, 1], default=1)
    s = sub.add_parser("score")
    _data_args(s, gold=True)
    s.add_argument("--run", required=True)
    s.add_argument("--output")
    args = cli.parse_args()
    if args.command == "audit":
        return audit(args)
    if args.command == "run":
        return run(args)
    return score(args)


if __name__ == "__main__":
    raise SystemExit(main())
