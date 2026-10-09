#!/usr/bin/env python3
"""Preregistered head-to-head: direct model call versus the double-loop harness.

Both arms use the same tunnel, model, reasoning effort and frozen case pool.
The direct arm receives every cutoff-eligible material in one call. The harness
arm runs ``run_double_loop_trace`` over the same pool. Gold labels are hashed at
registration, never read during ``run``, and opened only by ``score``, which
refuses a gold file whose hash differs from the registered one.

    python experiments/head_to_head.py register CASES.json --gold GOLD.json --output RUN
    python experiments/head_to_head.py run RUN --cases CASES.json --tunnel local
    python experiments/head_to_head.py score RUN --cases CASES.json --gold GOLD.json

Case file: {"benchmark_id": str, "cases": [{"id", "target", "materials",
"initial_version_ids", "config"?}]}; each case is a double-loop payload.
Gold file: {"benchmark_id": str, "labels": {case_id: {"truth": four-state
label, "original_version_ids": [version_id, ...] or null}}}. Where more than
one answer is acceptable (a record and its own same-URL revision, say), give a
list of alternatives: [["record-v2"], ["record-v1", "record-v2"]].

A win is declared only by the preregistered rule (strictly higher accuracy,
every output valid, token total within the registered ratio). Small case pools
remain exploratory; nothing here establishes real-news accuracy.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from newsverify import __version__  # noqa: E402
from newsverify.double_loop import run_double_loop_trace  # noqa: E402
from newsverify.model_runner import (  # noqa: E402
    COMMON, TUNNELS, VERIFY, _array, _object, exact_span, make_transport,
)
from newsverify.provenance import MaterialVersion, _material_eligibility, _time, from_mapping  # noqa: E402
from newsverify.tunnels import TunnelError  # noqa: E402

LABELS = ("supported", "contradicted", "conflicting", "unresolved")
ARMS = ("direct", "harness")
STRING = {"type": "string"}
DIRECT_SCHEMA = _object(
    verdict={"type": "string", "enum": list(LABELS)},
    basis=_array(_object(version_id=STRING, quote=STRING)),
    original_version_ids=_array(STRING),
    rationale=STRING,
)
DIRECT_INSTRUCTIONS = COMMON + VERIFY + """Also list original_version_ids: the version IDs of
supplied materials that are the original producing record of the target claim
(for example a primary measurement record, not a report that cites it), or []
if no supplied material is the original record.
"""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def git_commit() -> str | None:
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                   text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_cases(document) -> list[dict]:
    if not isinstance(document, dict) or not isinstance(document.get("benchmark_id"), str):
        raise ValueError("case file requires a benchmark_id string")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("case file requires a nonempty cases list")
    seen = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"].strip():
            raise ValueError(f"cases[{index}] requires a nonempty id")
        if case["id"] in seen:
            raise ValueError(f"duplicate case id {case['id']!r}")
        seen.add(case["id"])
        if not isinstance(case.get("target"), dict) or not isinstance(case.get("materials"), list) \
                or not case["materials"]:
            raise ValueError(f"case {case['id']!r} requires a target object and a materials list")
        for position, item in enumerate(case["materials"]):
            from_mapping(MaterialVersion, item, f"case {case['id']!r} materials[{position}]")
    return cases


def validate_gold(document, cases: list[dict]) -> dict[str, dict]:
    if not isinstance(document, dict) or not isinstance(document.get("labels"), dict):
        raise ValueError("gold file requires a labels object")
    labels = document["labels"]
    expected = {case["id"] for case in cases}
    if set(labels) != expected:
        raise ValueError("gold labels must cover exactly the registered case ids")
    for case in cases:
        label = labels[case["id"]]
        if not isinstance(label, dict) or label.get("truth") not in LABELS:
            raise ValueError(f"gold for {case['id']!r} requires a four-state truth label")
        origins = label.get("original_version_ids")
        version_ids = {item["version_id"] for item in case["materials"]}
        for alternative in acceptable_origin_sets(origins):
            if not alternative or any(value not in version_ids for value in alternative):
                raise ValueError(f"gold origins for {case['id']!r} must name supplied version ids or be null")
    return labels


def acceptable_origin_sets(origins) -> list[list[str]]:
    """Normalize ``null``, one list, or a list of alternative lists to a list of lists."""
    if origins is None:
        return []
    if not isinstance(origins, list) or not origins:
        raise ValueError("original_version_ids must be null, a nonempty list, or a list of lists")
    if all(isinstance(item, str) for item in origins):
        return [origins]
    if all(isinstance(item, list) and item and all(isinstance(v, str) for v in item) for item in origins):
        return origins
    raise ValueError("original_version_ids must be null, a nonempty list, or a list of lists")


# --- arms -------------------------------------------------------------------

def direct_packet(case: dict, scope: str = "pool") -> tuple[dict, dict[str, dict]]:
    """One packet of cutoff-eligible materials, verbatim; nothing later.

    ``scope="pool"`` hands the direct arm every eligible material: a
    same-evidence test of reasoning and bookkeeping. ``scope="initial"`` hands
    it only the case's ``initial_version_ids`` (what a user would paste), so the
    difference measured is the harness's retrieval from the pool; the registered
    summary says which was used, because the two answer different questions.
    """
    if scope not in {"pool", "initial"}:
        raise ValueError("direct scope must be pool or initial")
    cutoff = _time(case["target"].get("as_of"), "target.as_of")
    allowed = set(case.get("initial_version_ids") or []) if scope == "initial" else None
    eligible, excluded = [], {}
    for item in case["materials"]:
        material = from_mapping(MaterialVersion, item, "material")
        if allowed is not None and material.version_id not in allowed:
            continue
        reasons = _material_eligibility(material, cutoff)
        if reasons:
            excluded[material.version_id] = reasons
            continue
        eligible.append({"version_id": material.version_id, "url": material.url,
                         "issuer": material.issuer, "published_at": material.published_at,
                         "available_at": material.available_at,
                         "availability_basis": material.availability_basis,
                         "content": material.content})
    if not eligible:
        raise ValueError(f"case {case['id']!r} has no cutoff-eligible material")
    packet = {"target": {"text": case["target"]["text"], "as_of": case["target"]["as_of"],
                         "source_version_id": case["target"].get("source_version_id")},
              "materials": eligible, "excluded_version_ids": sorted(excluded),
              "evidence_scope": scope}
    return packet, {item["version_id"]: item for item in eligible}


def validate_direct(value: dict, materials: dict[str, dict]) -> None:
    if not isinstance(value, dict) or set(value) != set(DIRECT_SCHEMA["properties"]):
        raise ValueError("direct response fields do not match the schema")
    if value["verdict"] not in LABELS:
        raise ValueError("direct verdict is not a four-state label")
    if not isinstance(value["basis"], list) or not isinstance(value["original_version_ids"], list) \
            or not isinstance(value["rationale"], str):
        raise ValueError("direct response has wrong field types")
    for item in value["basis"]:
        if not isinstance(item, dict) or set(item) != {"version_id", "quote"}:
            raise ValueError("direct basis items need version_id and quote")
        exact_span(item["version_id"], item["quote"], materials)
    for version_id in value["original_version_ids"]:
        if version_id not in materials:
            raise ValueError("direct original_version_ids must name eligible materials")
    if value["verdict"] in {"supported", "contradicted"} and not value["basis"]:
        raise ValueError("a decisive direct verdict requires at least one quoted basis")


def run_direct(case: dict, transport, scope: str = "pool") -> dict:
    started = time.perf_counter()
    result = {"case_id": case["id"], "arm": "direct", "valid": False, "verdict": None,
              "original_version_ids": None, "error": None, "evidence_scope": scope}
    try:
        packet, materials = direct_packet(case, scope)
        result["packet"] = packet
        value = transport.generate("direct_verdict", DIRECT_INSTRUCTIONS, packet, DIRECT_SCHEMA)
        result["raw_response"] = value
        validate_direct(value, materials)
        result.update(valid=True, verdict=value["verdict"],
                      original_version_ids=sorted(set(value["original_version_ids"])),
                      rationale=value["rationale"], basis=value["basis"])
    except (TunnelError, ValueError, TypeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["calls"] = list(getattr(transport, "calls", []))
    result["wall_seconds"] = time.perf_counter() - started
    return result


def run_harness(case: dict, transport, max_model_calls: int, harness_config: dict | None = None) -> dict:
    started = time.perf_counter()
    payload = {key: case[key] for key in ("target", "materials", "initial_version_ids", "config")
               if key in case}
    if harness_config:
        payload["config"] = {**(payload.get("config") or {}), **harness_config}
    result = {"case_id": case["id"], "arm": "harness", "valid": False, "verdict": None,
              "original_version_ids": None, "error": None}
    try:
        report = run_double_loop_trace(payload, tunnel=transport.kind, transport=transport,
                                       max_model_calls=max_model_calls)
        named = sorted({item["version_id"] for item in report.get("origins", [])})
        result.update(valid=not report["errors"], verdict=report["fact_status"],
                      original_version_ids=named,
                      lineage_certified=report.get("provenance_status") == "original_material_located",
                      provenance_status=report["provenance_status"],
                      stop_reason=report["stop_reason"], errors=report["errors"],
                      usage=report["usage"], calls=report["execution"]["model_calls"],
                      blocked_calls=report["execution"].get("blocked_calls"))
        result["report"] = report
        if report["errors"]:
            result["error"] = "harness recorded errors; see errors"
    except (TunnelError, ValueError, TypeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["calls"] = list(getattr(transport, "calls", []))
    result["wall_seconds"] = time.perf_counter() - started
    return result


def tokens(calls: list[dict]) -> dict:
    total = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "failed_calls": 0, "missing_usage": 0,
             "input_chars": 0}
    for call in calls:
        total["calls"] += 1
        if not call.get("success"):
            total["failed_calls"] += 1
        if isinstance(call.get("input_chars"), int):
            total["input_chars"] += call["input_chars"]
        usage = call.get("usage") or {}
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, int):
                total[key] += value
            else:
                total["missing_usage"] += 1
    total["total_tokens"] = total["input_tokens"] + total["output_tokens"]
    total["mean_input_tokens_per_call"] = (total["input_tokens"] / total["calls"]) if total["calls"] else None
    return total


# --- commands ---------------------------------------------------------------

def parse_overrides(items: list[str] | None) -> dict:
    """``key=value`` pairs for TraceConfig, values parsed as JSON (true, 3) or kept as text."""
    overrides = {}
    for item in items or ():
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"harness config override must look like key=value, got {item!r}")
        try:
            overrides[key.strip()] = json.loads(value)
        except ValueError:
            overrides[key.strip()] = value
    return overrides


def command_register(arguments) -> int:
    document = read_json(arguments.cases)
    cases = validate_cases(document)
    if not arguments.gold.is_file():
        raise ValueError("gold file not found; it is hashed now and opened only by score")
    try:
        arguments.gold.resolve().relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise ValueError("keep the sealed gold file outside the repository during inference")
    # Shape is checked so a malformed gold file cannot void the run after inference;
    # the labels themselves are not written anywhere by this command.
    validate_gold(read_json(arguments.gold), cases)
    registration = {
        "benchmark_id": document["benchmark_id"], "registered_at": now(),
        "harness_version": __version__, "harness_commit": git_commit(),
        "cases_sha256": sha256_file(arguments.cases), "gold_sha256": sha256_file(arguments.gold),
        "case_order": [case["id"] for case in cases],
        "arm_order": [ARMS if index % 2 == 0 else ARMS[::-1] for index in range(len(cases))],
        "max_token_ratio": arguments.max_token_ratio, "max_model_calls": arguments.max_model_calls,
        "harness_config": parse_overrides(arguments.harness_config),
        "direct_scope": arguments.direct_scope,
        "comparison": ("same evidence: the direct arm receives every cutoff-eligible material"
                       if arguments.direct_scope == "pool" else
                       "retrieval: the direct arm receives only the initial materials; the harness "
                       "retrieves from the pool"),
        "success_rule": ("harness accuracy strictly greater than direct; every harness output valid; "
                         f"harness total tokens at most {arguments.max_token_ratio} x direct"),
    }
    write_json(arguments.output / "REGISTRATION.json", registration)
    print(f"Registered {len(cases)} cases in {arguments.output}; gold sealed by hash.")
    return 0


def _load_registration(run_dir: Path, cases_path: Path | None = None) -> dict:
    registration = read_json(run_dir / "REGISTRATION.json")
    if cases_path is not None and sha256_file(cases_path) != registration["cases_sha256"]:
        raise ValueError("case file changed since registration")
    return registration


def command_run(arguments) -> int:
    registration = _load_registration(arguments.run, arguments.cases)
    cases = {case["id"]: case for case in validate_cases(read_json(arguments.cases))}
    arms = ARMS if arguments.arm == "both" else (arguments.arm,)
    results = {arm: [] for arm in arms}
    resolved = {}
    for index, case_id in enumerate(registration["case_order"]):
        case = cases[case_id]
        for arm in registration["arm_order"][index]:
            if arm not in arms:
                continue
            transport = make_transport(arguments.tunnel, arguments.model, arguments.reasoning_effort,
                                       arguments.timeout)
            resolved.setdefault("model", getattr(transport, "model", arguments.model))
            resolved.setdefault("reasoning_effort", getattr(transport, "reasoning_effort",
                                                            arguments.reasoning_effort))
            if arm == "direct":
                result = run_direct(case, transport, registration.get("direct_scope", "pool"))
            else:
                result = run_harness(case, transport, registration["max_model_calls"],
                                     registration.get("harness_config") or None)
            result["tokens"] = tokens(result.get("calls", []))
            detail = {key: result.pop(key) for key in ("report", "packet", "raw_response") if key in result}
            if detail:
                write_json(arguments.run / "details" / f"{case_id}-{arm}.json",
                           {"case_id": case_id, "arm": arm, **detail})
            results[arm].append(result)
            print(f"{case_id} {arm}: verdict={result['verdict']} valid={result['valid']} "
                  f"tokens={result['tokens']['total_tokens']}", file=sys.stderr)
    settings = {"tunnel": arguments.tunnel, "model": resolved.get("model", arguments.model),
                "reasoning_effort": resolved.get("reasoning_effort", arguments.reasoning_effort),
                "timeout": arguments.timeout}
    for arm, items in results.items():
        write_json(arguments.run / f"predictions-{arm}.json", {
            "benchmark_id": registration["benchmark_id"], "arm": arm, "run_at": now(),
            "settings": settings, "cases_sha256": registration["cases_sha256"],
            "harness_commit": git_commit(), "results": items,
        })
    print(f"Wrote predictions for {', '.join(arms)} to {arguments.run}")
    return 0 if all(item["valid"] for items in results.values() for item in items) else 1


def score_arm(items: list[dict], labels: dict[str, dict]) -> dict:
    rows, correct, false_total, false_found, abstained = [], 0, 0, 0, 0
    traceable, origin_correct, invalid, certified = 0, 0, 0, 0
    for item in items:
        label = labels[item["case_id"]]
        valid = bool(item["valid"])
        invalid += not valid
        hit = valid and item["verdict"] == label["truth"]
        correct += hit
        if label["truth"] == "contradicted":
            false_total += 1
            false_found += hit
        abstained += valid and item["verdict"] == "unresolved"
        origin_hit = None
        alternatives = acceptable_origin_sets(label["original_version_ids"])
        if alternatives:
            traceable += 1
            named = sorted(item["original_version_ids"] or [])
            origin_hit = valid and any(named == sorted(option) for option in alternatives)
            origin_correct += origin_hit
        certified += bool(item.get("lineage_certified"))
        rows.append({"case_id": item["case_id"], "truth": label["truth"], "verdict": item["verdict"],
                     "valid": valid, "correct": hit, "origin_correct": origin_hit,
                     "total_tokens": item["tokens"]["total_tokens"], "calls": item["tokens"]["calls"],
                     "error": item.get("error")})
    total_tokens = sum(row["total_tokens"] for row in rows)
    total_calls = sum(item["tokens"]["calls"] for item in items)
    total_input_tokens = sum(item["tokens"]["input_tokens"] for item in items)
    total_input_chars = sum(item["tokens"].get("input_chars", 0) for item in items)
    total_output_tokens = sum(item["tokens"]["output_tokens"] for item in items)
    n = len(items)
    return {"cases": n, "correct": correct, "accuracy": correct / n if n else None,
            "false_claims": false_total, "false_claims_identified": false_found,
            "false_claim_recall": false_found / false_total if false_total else None,
            "abstained": abstained, "invalid_outputs": invalid,
            "traceable": traceable, "origin_correct": origin_correct,
            "origin_accuracy": origin_correct / traceable if traceable else None,
            "lineage_certified": certified,
            "total_tokens": total_tokens, "total_calls": total_calls,
            "mean_input_tokens_per_call": (total_input_tokens / total_calls) if total_calls else None,
            "packet_chars_sent": total_input_chars,
            # Rough provider-independent estimate of what the harness itself sent.
            "packet_only_tokens_estimate": round(total_input_chars / 4) + total_output_tokens,
            "rows": rows}


def command_score(arguments) -> int:
    registration = _load_registration(arguments.run, arguments.cases)
    if sha256_file(arguments.gold) != registration["gold_sha256"]:
        raise ValueError("gold file hash does not match the registration; refusing to score")
    predictions = {}
    for arm in ARMS:
        path = arguments.run / f"predictions-{arm}.json"
        if not path.is_file():
            raise ValueError(f"missing predictions for the {arm} arm")
        predictions[arm] = read_json(path)
        if predictions[arm]["cases_sha256"] != registration["cases_sha256"]:
            raise ValueError(f"{arm} predictions were made on a different case file")
    if predictions["direct"]["settings"] != predictions["harness"]["settings"]:
        raise ValueError("arms were run with different tunnel or model settings")
    labels = validate_gold(read_json(arguments.gold), validate_cases(read_json(arguments.cases)))
    expected = set(registration["case_order"])
    for arm, document in predictions.items():
        if {item["case_id"] for item in document["results"]} != expected:
            raise ValueError(f"{arm} predictions do not cover the registered cases")
    scores = {arm: score_arm(document["results"], labels) for arm, document in predictions.items()}
    direct, harness = scores["direct"], scores["harness"]
    ratio = (harness["total_tokens"] / direct["total_tokens"]) if direct["total_tokens"] else None
    packet_ratio = ((harness["packet_only_tokens_estimate"] / direct["packet_only_tokens_estimate"])
                    if direct["packet_only_tokens_estimate"] else None)
    verdict = {
        "accuracy_strictly_higher": (harness["accuracy"] or 0) > (direct["accuracy"] or 0),
        "all_harness_outputs_valid": harness["invalid_outputs"] == 0,
        "within_token_ratio": ratio is not None and ratio <= registration["max_token_ratio"],
        "token_ratio": ratio,
        "packet_only_token_ratio_estimate": packet_ratio,
        "token_rule_basis": "provider-reported input+output tokens, including any fixed per-call overhead",
    }
    verdict["harness_wins_by_registered_rule"] = all(
        verdict[key] for key in ("accuracy_strictly_higher", "all_harness_outputs_valid", "within_token_ratio"))
    paired = []
    direct_rows = {row["case_id"]: row for row in direct["rows"]}
    for row in harness["rows"]:
        other = direct_rows[row["case_id"]]
        paired.append({"case_id": row["case_id"], "truth": row["truth"], "direct": other["verdict"],
                       "harness": row["verdict"], "direct_correct": other["correct"],
                       "harness_correct": row["correct"], "direct_tokens": other["total_tokens"],
                       "harness_tokens": row["total_tokens"]})
    summary = {
        "benchmark_id": registration["benchmark_id"], "scored_at": now(),
        "registration": {key: registration.get(key) for key in
                         ("registered_at", "harness_version", "harness_commit", "success_rule",
                          "max_token_ratio", "max_model_calls", "harness_config", "direct_scope",
                          "comparison")},
        "settings": predictions["direct"]["settings"],
        "direct": {key: value for key, value in direct.items() if key != "rows"},
        "harness": {key: value for key, value in harness.items() if key != "rows"},
        "verdict": verdict, "paired_cases": paired,
        "limitations": [
            "A registered win on a small frozen pool is exploratory, not a population accuracy claim.",
            "Both arms see supplied snapshots only; neither arm retrieved from the open web.",
            "Historical cutoff isolation applies to supplied evidence, not to the model's training.",
            "Invalid or failed outputs count as incorrect; nothing was retried.",
        ],
    }
    write_json(arguments.run / "SUMMARY.json", summary)
    (arguments.run / "SUMMARY.md").write_text(render_summary(summary), encoding="utf-8")
    print(render_summary(summary))
    return 0


def render_summary(summary: dict) -> str:
    direct, harness, verdict = summary["direct"], summary["harness"], summary["verdict"]

    def pct(value):
        return "n/a" if value is None else f"{100 * value:.0f}%"

    ratio = "n/a" if verdict["token_ratio"] is None else f"{verdict['token_ratio']:.2f}x"
    packet = verdict.get("packet_only_token_ratio_estimate")
    packet_ratio = "n/a" if packet is None else f"{packet:.2f}x"

    def mean(arm):
        value = arm.get("mean_input_tokens_per_call")
        return "n/a" if value is None else f"{value:,.0f}"
    lines = [f"# Head-to-head: {summary['benchmark_id']}", "",
             f"Model `{summary['settings']['model']}` via `{summary['settings']['tunnel']}` "
             f"(reasoning effort `{summary['settings']['reasoning_effort']}`); "
             f"harness {summary['registration']['harness_version']} "
             f"({(summary['registration']['harness_commit'] or 'uncommitted')[:12]}); "
             f"harness config overrides `{json.dumps(summary['registration'].get('harness_config') or {})}`.", "",
             f"Comparison: {summary['registration'].get('comparison') or 'same evidence'}.", "",
             "| Measure | Direct | Harness |", "|---|---:|---:|",
             f"| Accuracy | {direct['correct']}/{direct['cases']} ({pct(direct['accuracy'])}) "
             f"| {harness['correct']}/{harness['cases']} ({pct(harness['accuracy'])}) |",
             f"| False-claim recall | {direct['false_claims_identified']}/{direct['false_claims']} "
             f"| {harness['false_claims_identified']}/{harness['false_claims']} |",
             f"| Origin named correctly (traceable) | {direct['origin_correct']}/{direct['traceable']} "
             f"| {harness['origin_correct']}/{harness['traceable']} |",
             f"| Lineage path certified by harness | n/a | {harness['lineage_certified']} |",
             f"| Abstained | {direct['abstained']} | {harness['abstained']} |",
             f"| Invalid outputs | {direct['invalid_outputs']} | {harness['invalid_outputs']} |",
             f"| Total tokens | {direct['total_tokens']:,} | {harness['total_tokens']:,} |",
             f"| Model calls | {direct['total_calls']} | {harness['total_calls']} |",
             f"| Mean input tokens per call | {mean(direct)} | {mean(harness)} |",
             f"| Packet chars sent by harness code | {direct['packet_chars_sent']:,} "
             f"| {harness['packet_chars_sent']:,} |", "",
             f"Token ratio harness/direct: {ratio} "
             f"(registered limit {summary['registration']['max_token_ratio']}x, provider-reported). "
             f"Packet-only estimate (chars/4 + output): {packet_ratio}. A mean input count far above "
             f"the packet size is the provider's fixed per-call overhead, paid once per call.", "",
             f"**Harness wins by the registered rule: {verdict['harness_wins_by_registered_rule']}** "
             f"(accuracy higher: {verdict['accuracy_strictly_higher']}, all valid: "
             f"{verdict['all_harness_outputs_valid']}, within tokens: {verdict['within_token_ratio']}).",
             "", "| Case | Truth | Direct | Harness |", "|---|---|---|---|"]
    for row in summary["paired_cases"]:
        lines.append(f"| {row['case_id']} | {row['truth']} | {row['direct'] or 'invalid'}"
                     f"{' ✓' if row['direct_correct'] else ''} | {row['harness'] or 'invalid'}"
                     f"{' ✓' if row['harness_correct'] else ''} |")
    lines += ["", *[f"- {item}" for item in summary["limitations"]], ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register", help="freeze the case pool and seal the gold file by hash")
    register.add_argument("cases", type=Path)
    register.add_argument("--gold", type=Path, required=True)
    register.add_argument("--output", type=Path, required=True)
    register.add_argument("--max-token-ratio", type=float, default=2.0)
    register.add_argument("--max-model-calls", type=int, default=12)
    register.add_argument("--direct-scope", choices=("pool", "initial"), default="pool",
                          help="what the direct arm receives: every eligible material (pool, a "
                               "same-evidence test) or only initial_version_ids (initial, a retrieval test)")
    register.add_argument("--harness-config", action="append", metavar="KEY=VALUE",
                          help="TraceConfig override applied to every case in the harness arm, e.g. "
                               "defer_verification_until_provenance_complete=true; recorded in the registration")
    run = sub.add_parser("run", help="run one or both arms with one tunnel and model")
    run.add_argument("run", type=Path)
    run.add_argument("--cases", type=Path, required=True)
    run.add_argument("--arm", choices=(*ARMS, "both"), default="both")
    run.add_argument("--tunnel", choices=TUNNELS, default="local")
    run.add_argument("--model")
    run.add_argument("--reasoning-effort")
    run.add_argument("--timeout", type=float, default=180)
    score = sub.add_parser("score", help="open the sealed gold file and score both arms")
    score.add_argument("run", type=Path)
    score.add_argument("--cases", type=Path, required=True)
    score.add_argument("--gold", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        return {"register": command_register, "run": command_run, "score": command_score}[arguments.command](arguments)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"head_to_head: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
