"""Offline V11-response replay against V12; never invokes a model or fetches data.

Only repeated decomposition calls for an already seen version are omitted.
Every retained response is read unchanged from its original CLI event file.
The output contains metadata and checks, not article bodies or model packets.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from newsverify.double_loop import run_double_loop_trace
from newsverify.news_tracing_runner import (
    _ResearchAdviceTransport, _located_origins, _observed_links,
)
from newsverify.provenance import MaterialVersion

STAGES = {"decompose", "verify", "select"}


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def cli_response(path):
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if any(e.get("type") in {"error", "turn.failed", "turn.incomplete", "turn.cancelled"}
           for e in events):
        raise ValueError("The source response has rejected CLI events")
    if sum(e.get("type") == "turn.completed" for e in events) != 1:
        raise ValueError("Expected exactly one completed source CLI turn")
    messages = [e["item"]["text"] for e in events if e.get("type") == "item.completed"
                and e.get("item", {}).get("type") == "agent_message"]
    if not messages:
        raise ValueError("The source CLI response is missing")
    return json.loads(messages[-1])


class ScriptedTransport:
    kind, model, reasoning_effort = "local", "offline-recorded-response", "low"

    def __init__(self, steps):
        self.steps = deepcopy(steps)
        self.inputs, self.calls, self.consumed = [], [], []

    def generate(self, stage, instructions, packet, schema):
        if not self.steps:
            raise ValueError("Unexpected additional replay request; no response synthesized")
        step = self.steps.pop(0)
        if stage != step["stage"]:
            raise ValueError("Replay stage differs from the unchanged response order")
        if stage == "decompose" and packet["material"]["version_id"] != step["version_id"]:
            raise ValueError("Replay selected a different material; no response adapted")
        self.inputs.append({"stage": stage, "packet": deepcopy(packet),
                            "original_call": step["original_call"]})
        self.consumed.append(step["original_call"])
        # These are offline dispatch records, not provider usage measurements.
        self.calls.append({"stage": stage, "success": True, "status": "completed",
                           "wall_seconds": 0, "usage": None})
        return deepcopy(step["response"])


def replay(source_run):
    workflow_path = source_run / "workflow.json"
    outer_path = source_run / "model-io.json"
    sources_path = source_run / "sources.json"
    result_path = source_run / "result.json"
    source_paths = [workflow_path, outer_path, sources_path, result_path]
    item = read(workflow_path)["results"][0]
    original_trace = item["claims"][0]["trace"]
    outer = read(outer_path)
    immutable = [x for x in item["execution"]["model_io"] if x["stage"] in STAGES]
    outer_native = [(i, x) for i, x in enumerate(outer, 1) if x["stage"] in STAGES]
    if len(immutable) != len(outer_native):
        raise ValueError("Immutable native snapshots and outer call indices do not align")
    steps, omitted, source_responses, seen = [], [], [], set()
    for snapshot, (index, outer_row) in zip(immutable, outer_native):
        matches = list((source_run / "private-cli-events").glob(f"{index:04d}-*.stdout"))
        if len(matches) != 1 or snapshot["stage"] != outer_row["stage"]:
            raise ValueError("Ambiguous source CLI call alignment")
        path = matches[0]
        source_paths.append(path)
        response = cli_response(path)
        if response != snapshot["response"]:
            raise ValueError("CLI response differs from its immutable workflow snapshot")
        version = snapshot["packet"].get("material", {}).get("version_id")
        entry = {"stage": snapshot["stage"], "original_call": index,
                 "version_id": version, "response": response}
        repeated = entry["stage"] == "decompose" and version in seen
        source_responses.append({"call_index_one_based": index, "stage": entry["stage"],
            "version_id": version, "cli_path": str(path), "cli_sha256": digest(path),
            "response_canonical_sha256": object_digest(response),
            "matches_immutable_workflow_response": True, "omitted_repeated_decomposition": repeated})
        if repeated:
            omitted.append(index)
            continue
        if entry["stage"] == "decompose":
            seen.add(version)
        steps.append(entry)
    before = {str(p): digest(p) for p in source_paths}
    code_paths = [REPO / "newsverify" / name for name in
                  ("double_loop.py", "provenance.py", "news_tracing_runner.py")]
    code_before = {str(p): digest(p) for p in code_paths}
    transport = ScriptedTransport(steps)
    advised = _ResearchAdviceTransport(transport, item["research_advice"],
                                      item["execution"].get("citation_resolutions", []))
    target = deepcopy(original_trace["target"])
    target["evidence_scope"] = tuple(target.get("evidence_scope", ()))
    payload = {"target": target, "materials": deepcopy(item["sources"]),
               "initial_version_ids": [target["source_version_id"]],
               "config": deepcopy(original_trace["config"])}
    # Exercise the current default; do not force the incremental switch here.
    payload["config"].pop("reanalyze_existing_versions", None)
    with patch("newsverify.tunnels.LocalTunnel.generate", side_effect=AssertionError("No model calls")), \
         patch("newsverify.tunnels.APITunnel.generate", side_effect=AssertionError("No model calls")):
        result = run_double_loop_trace(payload, transport=advised, max_model_calls=len(steps))

    materials = {m["version_id"]: m for m in item["sources"]}
    counts = Counter(h["version_id"] for h in result["analysis_history"] if h["accepted"])
    ecu_gap = "ecu-source-version"
    verification_gap = "verify-airburst-cause-date"
    ecu_steps = [s for s in steps if any(r["gap_id"] == ecu_gap
                                       for r in s["response"].get("resolutions", []))]
    ecu_version = ecu_steps[0]["version_id"] if len(ecu_steps) == 1 else None
    ecu_input = next((i for i, x in enumerate(transport.inputs)
                      if x["stage"] == "decompose" and
                      x["packet"]["material"]["version_id"] == ecu_version), None)
    earlier_checks = [x for x in transport.inputs[:ecu_input] if x["stage"] == "verify"]
    ecu_closures = [h for h in result["analysis_history"] if any(
        r["gap_id"] == ecu_gap for r in h["analysis"]["resolutions"])]
    packet_checks, verifier_seen = [], set()
    for entry in transport.inputs:
        stage, packet = entry["stage"], entry["packet"]
        if stage not in {"decompose", "verify"}:
            continue
        context = packet["context"]
        full = [m["version_id"] for m in context["materials"]]
        current = packet.get("material", {}).get("version_id")
        prior = context.get("prior_materials", [])
        prior_ids = [m["version_id"] for m in prior]
        serialized = json.dumps(packet, ensure_ascii=False)
        old_bodies_absent = all(json.dumps(materials[v]["content"], ensure_ascii=False)[1:-1]
                                not in serialized for v in prior_ids)
        metadata_valid = all("content" not in m and m["content_chars"] == len(materials[m["version_id"]]["content"])
            and m["content_sha256"] == hashlib.sha256(materials[m["version_id"]]["content"].encode()).hexdigest()
            for m in prior)
        only_new = not (set(full) & verifier_seen) if stage == "verify" else full == []
        if stage == "verify":
            verifier_seen.update(full)
        packet_checks.append({"original_call": entry["original_call"], "stage": stage,
            "current_version_id": current, "complete_context_version_ids": full,
            "prior_version_ids": prior_ids, "prior_metadata_hashes_valid": metadata_valid,
            "old_complete_bodies_absent_from_entire_packet": old_bodies_absent,
            "only_new_complete_materials_in_context": only_new,
            "serialized_packet_characters": len(serialized),
            "open_gap_ids": [g["id"] for g in context["gaps"]]})

    source_docs = read(sources_path)
    collector = SimpleNamespace(documents={s["url"]: SimpleNamespace(**s) for s in source_docs},
                                requests=read(result_path)["source_requests"])
    observed = _observed_links([MaterialVersion(**m) for m in item["sources"]], collector)
    claim = {"id": target["id"], "trace": result}
    located = _located_origins(claim, observed, item["execution"].get("citation_resolutions", []))
    previous_located = item["claims"][0]["located_sources"]
    exact_spans = []
    for relation in result["relations"]:
        for span in relation["basis"]:
            text = materials[span["version_id"]]["content"]
            exact_spans.append(text[span["start"]:span["end"]] == span["quote"])
    final_gap_ids = [g["id"] for g in result["gaps"]]
    checks = {
        "omitted_only_expected_seed_revisits": omitted == [14, 18],
        "unchanged_responses_consumed_in_order": transport.consumed == [s["original_call"] for s in steps],
        "no_unused_or_synthesized_responses": not transport.steps,
        "incremental_default_active": result["execution"]["incremental_source_analysis"] is True,
        "exactly_three_unique_decompositions": len(counts) == 3 and set(counts.values()) == {1},
        "ecu_gap_still_open_before_ecu_admission": bool(earlier_checks) and all(
            ecu_gap in {g["id"] for g in x["packet"]["context"]["gaps"]} for x in earlier_checks),
        "ecu_gap_closed_only_by_ecu_analysis": len(ecu_closures) == 1 and
            ecu_closures[0]["version_id"] == ecu_version and ecu_gap not in final_gap_ids,
        "ecu_resolution_quotes_admitted_ecu_text": bool(ecu_closures) and all(
            any(s["version_id"] == ecu_version for s in r["basis"])
            for r in ecu_closures[0]["analysis"]["resolutions"] if r["gap_id"] == ecu_gap),
        "verification_gap_retained": verification_gap in final_gap_ids,
        "all_native_relation_quotes_exact": bool(exact_spans) and all(exact_spans),
        "native_observed_link_path_still_valid": bool(located) and located == previous_located,
        "old_complete_materials_not_resent": all(x["old_complete_bodies_absent_from_entire_packet"]
            and x["prior_metadata_hashes_valid"] and x["only_new_complete_materials_in_context"] for x in packet_checks),
        "native_execution_without_errors": not result["errors"],
        "source_files_unchanged": all(digest(Path(p)) == h for p, h in before.items()),
        "runtime_code_stable_during_replay": all(digest(Path(p)) == h for p, h in code_before.items()),
    }
    return {"kind": "Offline replay of unchanged authoritative V11 responses; not a model result",
        "created_at": datetime.now(timezone.utc).isoformat(), "new_real_model_calls": 0,
        "valid": all(checks.values()), "checks": checks,
        "source_run": str(source_run), "source_file_sha256": before,
        "runtime_code_sha256": code_before, "source_response_records": source_responses,
        "omitted_repeated_decomposition_calls": omitted,
        "retained_original_call_order": transport.consumed,
        "offline_response_dispatches": len(transport.calls), "unconsumed_script_steps": len(transport.steps),
        "decompositions_by_version": dict(counts), "packet_checks": packet_checks,
        "replayed_provenance_status": result["provenance_status"],
        "replayed_fact_status": result["fact_status"], "replayed_stop_reason": result["stop_reason"],
        "replayed_errors": result["errors"], "retained_gap_ids": final_gap_ids,
        "located_origins": located,
        "direct_native_relations": [{k: r[k] for k in ("from_version", "to_version", "kind", "status")}
                                    for r in result["relations"] if r["status"] == "direct"],
        "authority_limit": "CLI responses match immutable workflow snapshots. No response content is adapted. "
            "V12 packets differ from original V11 inputs; this verifies compatibility and host invariants, "
            "not how a new model would respond. Full text may appear once per source in decomposition and "
            "once as newly admitted verification evidence; older complete texts are replaced by metadata and retained exact evidence."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, default=REPO.parent / "live-origin-v11-checkout" /
                        "experiments/live-origin-v11-20260914/private-run-01/a701-harness")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("PREFLIGHT_REPLAY.json"))
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.source_run.resolve()):
        parser.error("The replay output cannot overwrite anything in the source run")
    try:
        result = replay(args.source_run.resolve())
    except Exception as exc:
        result = {"kind": "Offline replay failed without adapting responses", "valid": False,
                  "new_real_model_calls": 0, "error_type": type(exc).__name__, "error": str(exc)}
    # Public replay metadata uses stable path aliases; original receipts stay private.
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    serialized = serialized.replace(str(args.source_run.resolve()), "<source-run>")
    serialized = serialized.replace(str(REPO.resolve()), "<checkout>")
    args.output.write_text(serialized + "\n")
    print(json.dumps({"output": str(args.output.resolve()), "valid": result["valid"],
                      "checks": result.get("checks"), "error": result.get("error")}, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
