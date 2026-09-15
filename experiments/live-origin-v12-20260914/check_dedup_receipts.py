#!/usr/bin/env python3
"""Audit one completed run's native no-repeat policy from private stdin receipts.

Usage: python3 -B check_dedup_receipts.py PRIVATE_RUN_DIRECTORY
       python3 -B check_dedup_receipts.py --self-test

Read-only, offline, standard library only. JSON output contains identities,
hashes, counts and error codes, never source text, prompts or response text.
This does not score primary-source identity, completeness, exact bases, active
gap lifecycle, final lineage, facts or prediction accuracy. Those require the
separate independent source/chain audit even when this checker passes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

NATIVE = {"decompose", "verify"}
MATERIAL_FIELDS = {"version_id", "url", "content", "retrieved_at", "published_at",
                   "available_at", "availability_basis", "issuer"}
PRIOR_FIELDS = MATERIAL_FIELDS - {"content"} | {"content_sha256", "content_chars"}
SCOPE = {
    "unit": "Each immutable publisher version, separately within native decompose and verify.",
    "experiment_scope": "The registered V12 arms each contain one native claim. Counts aggregate within "
                        "an arm; this is not a policy for future multi-claim batches.",
    "counting": "Every matched private request receipt counts, including failed dispatch attempts; "
                "blocked-dispatch rows do not. A receipt proves submitted-input preparation and a "
                "transport attempt, not that the remote model consumed it. Missing receipts fail closed.",
    "identity": "URL, content, issuer, publication and availability metadata; local version ID and "
                "retrieval time are excluded from alias identity. Changed content/metadata stays distinct.",
    "retained_evidence": "Previously accepted analyses and exact evidence may recur. They are not "
                         "counted as newly supplied full-material fields, even for short source texts.",
    "excluded_stages": "Full news research, source selection and shared final forecast remain separate.",
    "independent_source_chain_audit_required": True,
    "does_not_establish_trace_success": True,
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def object_hash(value):
    return digest(canonical(value).encode("utf-8"))


def immutable(material):
    return {k: v for k, v in material.items() if k not in {"version_id", "retrieved_at"}}


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


class ArmAudit:
    def __init__(self, directory):
        self.directory = directory
        self.checks, self.errors, self.files = {}, [], {}
        self.packets, self.versions, self.traces = [], {}, {}
        self.exposures = []
        self.counts = {stage: Counter() for stage in NATIVE}
        self.identities = {stage: Counter() for stage in NATIVE}

    def check(self, code, condition, **where):
        passed = bool(condition)
        self.checks[code] = self.checks.get(code, True) and passed
        if not passed:
            self.errors.append({"code": code, **where})
        return passed

    def read(self, path, *, optional=False):
        if optional and not path.exists():
            return None
        name = str(path.relative_to(self.directory))
        try:
            before = path.stat()
            raw = path.read_bytes()  # Each input file is read once; no polling or rewriting.
            after = path.stat()
            self.files[name] = digest(raw)
            self.check("input_stable_during_read", (before.st_size, before.st_mtime_ns) ==
                       (after.st_size, after.st_mtime_ns), file=name)
            return strict_json(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError, RecursionError):
            self.check("readable_unambiguous_json", False, file=name)
            return None

    def add_material(self, material, *, call=None):
        if not self.check("full_material_shape", isinstance(material, dict) and
                          set(material) == MATERIAL_FIELDS and
                          all(isinstance(material.get(k), str) and material[k]
                              for k in ("version_id", "content", "url")), call=call):
            return None
        version = material["version_id"]
        old = self.versions.get(version)
        self.check("version_identity_no_collision", old is None or immutable(old) == immutable(material),
                   call=call, version_id=version)
        if old is None:
            self.versions[version] = material
        return version

    def load_traces(self, workflow):
        if not isinstance(workflow, dict) or not isinstance(workflow.get("results"), list):
            return
        for item in workflow["results"]:
            if not isinstance(item, dict):
                self.check("workflow_shape", False)
                continue
            for material in item.get("sources", []):
                self.add_material(material)
            for claim in item.get("claims", []):
                trace = claim.get("trace") if isinstance(claim, dict) else None
                if not isinstance(trace, dict):
                    continue
                target = trace.get("target", {}).get("id")
                if not self.check("unique_trace_target", isinstance(target, str) and target not in self.traces):
                    continue
                for material in trace.get("materials", []):
                    self.add_material(material)
                accepted = [h for h in trace.get("analysis_history", []) if h.get("accepted") is True]
                counts = Counter(h.get("version_id") for h in accepted)
                self.check("one_accepted_analysis_per_version", all(n == 1 for n in counts.values()), target_id=target)
                self.check("accepted_history_never_revisit", all(h.get("revisit") is False and
                           h.get("duplicate") is False for h in accepted), target_id=target)
                expected = {h["version_id"]: h["analysis"] for h in accepted}
                self.check("final_analyses_preserved", trace.get("analyses") == expected, target_id=target)
                self.check("incremental_policy_enabled", trace.get("config", {}).get("reanalyze_existing_versions") is False
                           and trace.get("execution", {}).get("incremental_source_analysis") is True, target_id=target)
                operations = trace.get("operations", [])
                self.check("no_actual_revisit_operations", all(
                    op.get("action") not in {"upstream_arrival_reanalysis", "revisit_requested"}
                    and not (op.get("action") == "decompose_started" and op.get("revisit"))
                    for op in operations), target_id=target)
                aliases = trace.get("execution", {}).get("pool_aliases_not_admitted", {})
                admitted = set(trace.get("eligible_version_ids", []))
                self.check("aliases_not_forged_as_admitted", not (set(aliases) & admitted), target_id=target)
                for alias, representative in aliases.items():
                    self.check("aliases_match_same_immutable_version", alias in self.versions and
                               representative in self.versions and immutable(self.versions[alias]) ==
                               immutable(self.versions[representative]), target_id=target, version_id=alias)
                self.traces[target] = {"trace": trace, "accepted": expected, "seen": {}, "verified": set(),
                                       "verify_history": [], "used_history": [], "accepted_order": list(expected),
                                       "revisit_requests": Counter(), "skipped": Counter(
                                           op.get("version_id") for op in operations if
                                           op.get("action") == "reanalysis_skipped" and op.get("trigger") == "model_request")}

    def prior(self, material, call):
        if not self.check("prior_metadata_only", isinstance(material, dict) and set(material) == PRIOR_FIELDS,
                          call=call):
            return None
        version = material.get("version_id")
        original = self.versions.get(version)
        valid = original is not None and all(material.get(k) == v for k, v in original.items()
                                             if k not in {"content", "retrieved_at"})
        valid = valid and material["content_chars"] == len(original["content"]) and \
            material["content_sha256"] == digest(original["content"].encode("utf-8"))
        self.check("prior_metadata_hash_matches_original", valid, call=call, version_id=version)
        return version

    def native(self, number, stage, packet, row):
        context = packet.get("context")
        target = packet.get("target", {}).get("id")
        state = self.traces.get(target)
        usable_context = context if isinstance(context, dict) else {}
        materials, prior = usable_context.get("materials"), usable_context.get("prior_materials")
        current = packet.get("material") if stage == "decompose" else None
        exposed = ([current] if stage == "decompose" else (materials if isinstance(materials, list) else []))
        valid_exposed = [(self.add_material(m, call=number), m) for m in exposed]
        valid_exposed = [(v, m) for v, m in valid_exposed if v is not None]
        ids = [v for v, _ in valid_exposed]
        full_receipts = []
        for version, material in valid_exposed:
            self.counts[stage][version] += 1
            identity = object_hash(immutable(material))
            self.identities[stage][identity] += 1
            receipt = {"version_id": version, "url": material["url"],
                       "content_sha256": digest(material["content"].encode("utf-8")),
                       "content_chars": len(material["content"]),
                       "available_at": material["available_at"],
                       "availability_basis_sha256": object_hash(material["availability_basis"]),
                       "immutable_identity_sha256": identity}
            full_receipts.append(receipt)
            self.exposures.append({"call": number, **receipt})
        # Count receipt-proven exposure even when failure prevented workflow.json
        # from being written. Missing host state cannot turn that attempt into zero.
        packet_receipt = {"call": number, "stage": stage, "target_id": target,
            "full_version_ids": ids, "failed_attempt": row.get("status") == "failed",
            "full_material_receipts": full_receipts, "host_state_available": state is not None}
        self.packets.append(packet_receipt)
        if not self.check("native_trace_and_context_available", state is not None and isinstance(context, dict), call=number):
            return
        if not self.check("incremental_packet_lists", isinstance(materials, list) and isinstance(prior, list), call=number):
            return
        prior_ids = [self.prior(m, number) for m in prior]
        self.check("unique_materials_in_packet", len(set(ids)) == len(ids) and len(set(prior_ids)) == len(prior_ids), call=number)
        self.check("prior_analyses_preserved_in_packet", context.get("analyses") == state["seen"], call=number)
        self.check("prior_verdicts_preserved_in_packet", context.get("verification_history") == state["verify_history"], call=number)
        self.check("verified_ids_preserved_in_packet", context.get("verified_version_ids") == sorted(state["verified"]), call=number)
        if stage == "decompose":
            self.check("decompose_no_context_full_bodies", materials == [], call=number)
            self.check("decompose_has_all_prior_metadata", set(prior_ids) == set(state["seen"]), call=number)
        else:
            self.check("verify_only_new_full_bodies", set(ids) == set(state["seen"]) - state["verified"], call=number)
            self.check("verify_has_all_prior_metadata", set(prior_ids) == state["verified"], call=number)
        # Do not allow hidden full-material fields outside the two declared locations.
        def content_paths(value, path=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "content":
                        yield path + (key,)
                    yield from content_paths(child, path + (key,))
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    yield from content_paths(child, path + (i,))
        allowed = {("material", "content")} if stage == "decompose" else {
            ("context", "materials", i, "content") for i in range(len(materials))}
        self.check("full_content_fields_only_at_declared_locations", set(content_paths(packet)) == allowed, call=number)
        packet_receipt.update({"prior_version_ids": prior_ids,
            "prior_analysis_sha256": {v: object_hash(a) for v, a in state["seen"].items()},
            "prior_verdict_count": len(state["verify_history"])})
        if stage == "decompose" and row.get("status") == "completed" and len(ids) == 1:
            version = ids[0]
            if version in state["accepted"]:
                state["used_history"].append(version)
                state["seen"][version] = state["accepted"][version]
                state["revisit_requests"].update(state["accepted"][version].get("revisit_versions", []))
        if stage == "verify" and row.get("status") == "completed":
            round_number = context.get("usage", {}).get("rounds")
            accepted = [h for h in state["trace"].get("verification_history", []) if h.get("round") == round_number]
            self.check("verification_history_unambiguous", len(accepted) <= 1, call=number)
            if len(accepted) == 1:
                state["verify_history"].extend(accepted)
                state["verified"].update(state["seen"])

    def run(self):
        calls = self.read(self.directory / "calls.json")
        rows = self.read(self.directory / "model-io.json")
        sources = self.read(self.directory / "sources.json")
        workflow = self.read(self.directory / "workflow.json", optional=True)
        blocked = self.read(self.directory / "blocked-dispatches.json", optional=True) or []
        self.load_traces(workflow)
        if not isinstance(calls, list) or not isinstance(rows, list):
            self.check("call_journal_lists", False)
            calls, rows = [], []
        self.check("call_journal_lengths_match", len(calls) == len(rows))
        receipts = defaultdict(list)
        for path in sorted((self.directory / "private-cli-events").glob("*.request.json")):
            match = re.fullmatch(r"(\d{4,})-\d+\.request\.json", path.name)
            if self.check("receipt_filename_has_call_index", match is not None, file=path.name):
                receipts[int(match[1])].append(path)
        self.check("no_orphan_or_duplicate_receipts", set(receipts) <= set(range(1, len(calls) + 1))
                   and all(len(v) == 1 for v in receipts.values()))
        for number, (call, row) in enumerate(zip(calls, rows), 1):
            if not self.check("call_record_objects", isinstance(call, dict) and isinstance(row, dict), call=number):
                continue
            self.check("call_stage_and_terminal_status_match", call.get("stage") == row.get("stage") and
                       row.get("status") in {"completed", "failed"} and call.get("status") == row.get("status")
                       and call.get("success") is (row.get("status") == "completed"), call=number)
            if not self.check("one_request_receipt_per_attempt", len(receipts[number]) == 1, call=number):
                continue
            receipt = self.read(receipts[number][0])
            if not self.check("request_receipt_shape", isinstance(receipt, dict) and
                              isinstance(receipt.get("stdin"), str), call=number):
                continue
            stdin = receipt["stdin"]
            actual_hash = digest(stdin.encode("utf-8"))
            self.check("stdin_hash_matches_receipt_and_call", actual_hash == receipt.get("stdin_sha256") ==
                       call.get("input_sha256"), call=number)
            self.check("receipt_schema_and_model_match", receipt.get("schema") == row.get("schema") and
                       receipt.get("model") == call.get("model") and
                       receipt.get("reasoning_effort") == call.get("reasoning_effort"), call=number)
            try:
                prefix = row["instructions"] + "\nEVIDENCE PACKET:\n"
                expected = prefix + json.dumps(row["packet"], ensure_ascii=False, allow_nan=False)
                self.check("stdin_exactly_matches_immutable_journal", stdin == expected, call=number)
                if not stdin.startswith(prefix):
                    raise ValueError("Unexpected stdin framing")
                packet = strict_json(stdin[len(prefix):])
                if not isinstance(packet, dict):
                    raise ValueError("Packet must be an object")
            except (KeyError, TypeError, ValueError, RecursionError):
                self.check("actual_stdin_packet_decodable", False, call=number)
                continue
            if row.get("stage") in NATIVE:
                self.native(number, row["stage"], packet, row)
        for stage in sorted(NATIVE):
            self.check(stage + "_at_most_once_per_version", all(v <= 1 for v in self.counts[stage].values()))
            self.check(stage + "_at_most_once_per_immutable_alias", all(v <= 1 for v in self.identities[stage].values()))
        for target, state in self.traces.items():
            self.check("all_accepted_analyses_match_actual_dispatch_order", state["used_history"] == state["accepted_order"], target_id=target)
            self.check("all_accepted_verdicts_match_actual_dispatches", state["verify_history"] ==
                       state["trace"].get("verification_history", []), target_id=target)
            self.check("all_model_revisits_recorded_skipped", state["revisit_requests"] == state["skipped"], target_id=target)
        source_lookup = defaultdict(list)
        for source in sources or []:
            if isinstance(source, dict) and isinstance(source.get("url"), str):
                source_lookup[source["url"]].append(source)
        exposed_versions = set().union(*(set(c) for c in self.counts.values()))
        for material in self.exposures:
            self.check("native_full_body_matches_saved_source", any(
                isinstance(source.get("content"), str) and material["content_sha256"] == digest(source["content"].encode("utf-8"))
                and material["content_chars"] == len(source["content"])
                and material["available_at"] == source.get("available_at")
                and material["availability_basis_sha256"] == object_hash(source.get("availability_basis"))
                for source in source_lookup[material["url"]]), call=material["call"], version_id=material["version_id"])
        self.check("blocked_dispatches_separate", isinstance(blocked, list) and
                   all(isinstance(b, dict) and isinstance(b.get("stage"), str) and b.get("blocked_by") for b in blocked))
        exercised = bool(self.packets)
        stage_coverage = {stage: bool(counts) for stage, counts in sorted(self.counts.items())}
        return {"arm": self.directory.name, "valid": not self.errors,
            "dedup_status": "failed" if self.errors else ("passed" if all(stage_coverage.values())
                            else ("partial" if exercised else "not_exercised")),
            "summary": {"journal_attempts": len(calls), "request_receipts": sum(map(len, receipts.values())),
                "failed_attempts": sum(c.get("success") is False for c in calls if isinstance(c, dict)),
                "blocked_not_counted": len(blocked) if isinstance(blocked, list) else None,
                "native_packet_count": len(self.packets), "native_trace_count": len(self.traces),
                "stage_coverage": stage_coverage},
            "checks": self.checks, "errors": self.errors,
            "full_exposures_by_version": {s: dict(sorted(c.items())) for s, c in sorted(self.counts.items())},
            "full_exposures_by_immutable_identity": {s: dict(sorted(c.items())) for s, c in sorted(self.identities.items())},
            "sources": [{"version_id": v, "url": self.versions[v]["url"],
                "content_sha256": digest(self.versions[v]["content"].encode()),
                "content_chars": len(self.versions[v]["content"]),
                "available_at": self.versions[v]["available_at"],
                "immutable_identity_sha256": object_hash(immutable(self.versions[v]))} for v in sorted(exposed_versions)],
            "packet_checks": self.packets, "input_file_sha256": self.files}


def audit_run(directory):
    run_audit = ArmAudit(directory)
    manifest = run_audit.read(directory / "manifest.json")
    run_audit.read(directory / "COMPLETED.json")
    arms = sorted(p for p in directory.iterdir() if p.is_dir() and p.name.endswith("-harness"))
    registration = manifest.get("registration", {}) if isinstance(manifest, dict) else {}
    seeds = registration.get("seed_text_sha256", {})
    expected = {case + "-harness" for case in seeds} if isinstance(seeds, dict) else set()
    run_audit.check("all_registered_harness_arms_present", bool(expected) and {p.name for p in arms} == expected)
    results = []
    for arm in arms:
        audit = ArmAudit(arm)
        try:
            result = audit.run()
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            audit.check("audit_input_shape", False)
            result = {"arm": arm.name, "valid": False, "dedup_status": "failed",
                      "checks": audit.checks, "errors": audit.errors, "input_file_sha256": audit.files}
        results.append(result)
    errors = run_audit.errors
    return {"kind": "V12 independent native request-receipt dedup audit", "scope": SCOPE,
        "valid": bool(arms) and not errors and all(a["valid"] for a in results),
        "summary": {"harness_arms": len(arms), "dedup_passed": sum(a["dedup_status"] == "passed" for a in results),
                    "dedup_failed": sum(a["dedup_status"] == "failed" for a in results),
                    "partially_exercised": sum(a["dedup_status"] == "partial" for a in results),
                    "not_exercised": sum(a["dedup_status"] == "not_exercised" for a in results),
                    "source_trace_success": None}, "checks": run_audit.checks,
        "input_file_sha256": run_audit.files, "errors": errors, "arms": results}


def self_test():
    # Generate real incremental host packets with scripted responses, then write
    # synthetic stdin receipts using the actual framing. No tunnel is invoked.
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from newsverify.double_loop import run_double_loop_trace
    from tests.test_double_loop import ScriptedTransport
    from tests.test_incremental_double_loop import incremental_payload, incremental_script

    class Checks(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory()
            self.addCleanup(self.temp.cleanup)
            self.run = Path(self.temp.name)
            self.arm = self.run / "synthetic-harness"
            (self.arm / "private-cli-events").mkdir(parents=True)
            (self.run / "COMPLETED.json").write_text("{}")
            (self.run / "manifest.json").write_text(json.dumps({"registration": {
                "seed_text_sha256": {"synthetic": "fixture"}}}))
            data = incremental_payload()
            transport = ScriptedTransport(incremental_script())
            trace = run_double_loop_trace(data, transport=transport)
            self.assertFalse(trace["errors"])
            self.rows = [{**row, "status": "completed", "schema": {}, "response": {}}
                         for row in transport.inputs]
            self.workflow = {"results": [{"sources": data["materials"], "claims": [{"trace": trace}]}]}
            self.sources = [{k: m[k] for k in ("url", "content", "available_at", "availability_basis")}
                            for m in data["materials"]]
            self.write()

        def write(self):
            def save(path, value):
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            calls = []
            for i, row in enumerate(self.rows, 1):
                stdin = row["instructions"] + "\nEVIDENCE PACKET:\n" + json.dumps(row["packet"], ensure_ascii=False)
                sha = digest(stdin.encode())
                calls.append({"stage": row["stage"], "model": "synthetic", "reasoning_effort": "low",
                              "status": row["status"], "success": row["status"] == "completed", "input_sha256": sha})
                save(self.arm / "private-cli-events" / f"{i:04d}-1.request.json",
                     {"stdin": stdin, "stdin_sha256": sha, "schema": row["schema"], "model": "synthetic", "reasoning_effort": "low"})
            for name, value in (("calls.json", calls), ("model-io.json", self.rows),
                                ("workflow.json", self.workflow), ("sources.json", self.sources)):
                save(self.arm / name, value)

        def result(self):
            return audit_run(self.run)

        def codes(self):
            return {e["code"] for e in self.result()["arms"][0]["errors"]}

        def test_actual_incremental_packets_and_short_exact_evidence_pass(self):
            result = self.result()
            self.assertTrue(result["valid"], result)
            self.assertEqual(1, result["summary"]["dedup_passed"])
            self.assertEqual({"notice": 1, "record": 1}, result["arms"][0]["full_exposures_by_version"]["decompose"])
            rendered = json.dumps(result)
            self.assertNotIn(self.sources[0]["content"], rendered)
            self.assertIsNone(result["summary"]["source_trace_success"])

        def test_failed_duplicate_dispatch_is_counted(self):
            duplicate = deepcopy(self.rows[0])
            duplicate["status"] = "failed"
            self.rows.append(duplicate)
            self.write()
            self.assertIn("decompose_at_most_once_per_version", self.codes())
            self.assertEqual(1, self.result()["arms"][0]["summary"]["failed_attempts"])

        def test_failed_duplicate_verifier_full_body_is_counted(self):
            duplicate = deepcopy(next(r for r in self.rows if r["stage"] == "verify"))
            duplicate["status"] = "failed"
            self.rows.append(duplicate)
            self.write()
            self.assertIn("verify_at_most_once_per_version", self.codes())

        def test_changed_body_under_same_id_keeps_actual_receipt_hash(self):
            material = self.rows[0]["packet"]["material"]
            material["content"] = "A truncated or substituted body."
            self.write()
            result = self.result()["arms"][0]
            codes = {e["code"] for e in result["errors"]}
            self.assertIn("version_identity_no_collision", codes)
            self.assertIn("native_full_body_matches_saved_source", codes)
            receipt = result["packet_checks"][0]["full_material_receipts"][0]
            self.assertEqual(digest(material["content"].encode()), receipt["content_sha256"])

        def test_blocked_request_is_excluded(self):
            (self.arm / "blocked-dispatches.json").write_text(json.dumps([
                {"stage": "decompose", "blocked_by": "earlier_failure"}]))
            result = self.result()
            self.assertTrue(result["valid"])
            self.assertEqual(1, result["arms"][0]["summary"]["blocked_not_counted"])

        def test_tampered_stdin_hash_and_journal_are_rejected(self):
            path = self.arm / "private-cli-events/0001-1.request.json"
            receipt = json.loads(path.read_text())
            receipt["stdin"] += " "
            path.write_text(json.dumps(receipt))
            self.assertIn("stdin_hash_matches_receipt_and_call", self.codes())
            self.assertIn("stdin_exactly_matches_immutable_journal", self.codes())

        def test_missing_or_duplicate_receipt_is_not_silently_ignored(self):
            path = self.arm / "private-cli-events/0001-1.request.json"
            path.rename(path.with_name("0001-2.request.json"))
            self.assertTrue(self.result()["valid"])
            path.write_text("{}")
            self.assertIn("no_orphan_or_duplicate_receipts", self.codes())

        def test_missing_receipt_fails_closed(self):
            (self.arm / "private-cli-events/0001-1.request.json").rename(self.arm / "not-a-receipt.json")
            self.assertIn("one_request_receipt_per_attempt", self.codes())

        def test_missing_workflow_does_not_hide_failed_fulltext_attempt(self):
            self.rows = [self.rows[0]]
            self.rows[0]["status"] = "failed"
            self.write()
            # Other existing receipts are intentionally removed only inside this
            # isolated synthetic directory, never in a supplied run directory.
            for p in (self.arm / "private-cli-events").glob("*.request.json"):
                if not p.name.startswith("0001-"):
                    p.unlink()
            (self.arm / "workflow.json").rename(self.arm / "unwritten-workflow.json")
            result = self.result()["arms"][0]
            self.assertFalse(result["valid"])
            self.assertEqual(1, result["summary"]["native_packet_count"])
            self.assertEqual({"notice": 1}, result["full_exposures_by_version"]["decompose"])

        def test_unexercised_verify_does_not_report_complete_pass(self):
            self.rows = [self.rows[0]]
            trace = self.workflow["results"][0]["claims"][0]["trace"]
            trace["analysis_history"] = trace["analysis_history"][:1]
            trace["analyses"] = {"notice": trace["analyses"]["notice"]}
            trace["verification_history"] = []
            trace["operations"] = []
            self.write()
            for p in (self.arm / "private-cli-events").glob("*.request.json"):
                if not p.name.startswith("0001-"):
                    p.unlink()
            result = self.result()
            self.assertTrue(result["valid"], result)
            self.assertEqual("partial", result["arms"][0]["dedup_status"])

        def test_missing_registered_arm_is_rejected(self):
            (self.run / "manifest.json").write_text(json.dumps({"registration": {
                "seed_text_sha256": {"synthetic": "fixture", "missing": "fixture"}}}))
            self.assertFalse(self.result()["valid"])
            self.assertIn("all_registered_harness_arms_present", {e["code"] for e in self.result()["errors"]})

        def test_old_body_or_changed_metadata_in_prior_is_rejected(self):
            row = next(r for r in self.rows if r["stage"] == "decompose" and r["packet"]["context"]["prior_materials"])
            prior = row["packet"]["context"]["prior_materials"][0]
            prior["content"] = self.sources[0]["content"]
            self.write()
            self.assertIn("prior_metadata_only", self.codes())
            del prior["content"]
            prior["content_sha256"] = "0" * 64
            self.write()
            self.assertIn("prior_metadata_hash_matches_original", self.codes())

        def test_dropped_analysis_and_revisit_execution_are_rejected(self):
            trace = self.workflow["results"][0]["claims"][0]["trace"]
            del trace["analyses"]["notice"]
            trace["analysis_history"][0]["revisit"] = True
            self.write()
            self.assertIn("final_analyses_preserved", self.codes())
            self.assertIn("accepted_history_never_revisit", self.codes())

        def test_hidden_version_alias_cannot_evade_counts(self):
            row = deepcopy(self.rows[0])
            row["status"] = "failed"
            row["packet"]["material"]["version_id"] = "new-local-alias"
            self.rows.append(row)
            self.write()
            self.assertIn("decompose_at_most_once_per_immutable_alias", self.codes())

        def test_same_url_changed_body_is_distinct_identity(self):
            row = deepcopy(self.rows[0])
            row["status"] = "failed"
            row["packet"]["material"]["version_id"] = "new-edition"
            row["packet"]["material"]["content"] += " Revised body."
            self.rows.append(row)
            self.write()
            self.assertNotIn("decompose_at_most_once_per_immutable_alias", self.codes())
            self.assertIn("native_full_body_matches_saved_source", self.codes())

        def test_same_url_saved_different_bodies_both_match(self):
            row = deepcopy(self.rows[0])
            row["status"] = "failed"
            revised = row["packet"]["material"]
            revised.update(version_id="new-edition", content=revised["content"] + " Revised body.")
            self.sources.append({k: revised[k] for k in ("url", "content", "available_at", "availability_basis")})
            self.rows.append(row)
            self.write()
            codes = self.codes()
            self.assertNotIn("native_full_body_matches_saved_source", codes)
            self.assertNotIn("decompose_at_most_once_per_immutable_alias", codes)

    outcome = unittest.TextTestRunner(stream=sys.stderr).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    return {"kind": "Offline synthetic request-receipt regression", "valid": outcome.wasSuccessful(),
            "tests": outcome.testsRun, "failures": len(outcome.failures), "errors": len(outcome.errors),
            "real_model_calls": 0, "scope": SCOPE}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test == (args.run_directory is not None):
        parser.error("Choose one run_directory or --self-test")
    if args.run_directory is not None and not args.run_directory.is_dir():
        parser.error("run_directory must be an existing directory")
    result = self_test() if args.self_test else audit_run(args.run_directory.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
