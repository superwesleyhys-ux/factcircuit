#!/usr/bin/env python3
"""Score a completed frozen run, including abstentions and failures."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from run_test import HERE, POLICIES, read, dump, digest, sha256, validate_setup, validate_response, request_for, now

def usage(rows):
    calls = [c for r in rows for c in r.get("calls", [])]
    known = [c["usage"] for c in calls if isinstance(c.get("usage"), dict)
             and all(type(c["usage"].get(k)) is int for k in ("input_tokens", "output_tokens"))]
    complete = len(known) == len(calls) and bool(calls)
    return {"calls": len(calls), "successful_calls": sum(c.get("success") is True for c in calls),
            "known_usage_calls": len(known), "usage_complete": complete,
            "known_input_tokens": sum(c["input_tokens"] for c in known),
            "known_output_tokens": sum(c["output_tokens"] for c in known),
            "known_total_tokens": sum(c["input_tokens"] + c["output_tokens"] for c in known),
            "input_tokens": sum(c["input_tokens"] for c in known) if complete else None,
            "output_tokens": sum(c["output_tokens"] for c in known) if complete else None,
            "total_tokens": sum(c["input_tokens"] + c["output_tokens"] for c in known) if complete else None}

def score_arm(rows, gold, cases):
    details = []
    for row in rows:
        cid = row["case_id"]
        valid = row.get("status") == "completed"
        try:
            validate_response(row.get("result"), cases[cid]["packet"])
        except (ValueError, TypeError):
            valid = False
        result = row["result"] if valid else {}
        target = gold[cid]
        risk = result.get("risk")
        details.append({"case_id": cid, "valid": valid, "risk": risk,
                        "outcome_target": target["outcome_risk_target"],
                        "outcome_match": valid and risk == target["outcome_risk_target"],
                        "cutoff_state": result.get("cutoff_assessment"),
                        "cutoff_state_target": target["cutoff_assessment_target"],
                        "cutoff_state_match": valid and result.get("cutoff_assessment") == target["cutoff_assessment_target"],
                        "unsupported_fabrication_assertion": valid and result.get("fabrication_established") is True
                        and target["fabrication_established_target"] is False})
    pos = [d for d in details if d["outcome_target"] == "elevated"]
    neg = [d for d in details if d["outcome_target"] == "ordinary"]
    tp = sum(d["risk"] == "elevated" for d in pos)
    tn = sum(d["risk"] == "ordinary" for d in neg)
    return {"cases": len(details), "valid_outputs": sum(d["valid"] for d in details),
            "outcome_matches": sum(d["outcome_match"] for d in details),
            "outcome_accuracy": sum(d["outcome_match"] for d in details) / len(details),
            "positive_denominator": len(pos), "negative_denominator": len(neg),
            "true_positives": tp, "true_negatives": tn,
            "false_positives": sum(d["risk"] == "elevated" for d in neg),
            "false_negatives": sum(d["risk"] == "ordinary" for d in pos),
            "abstentions": sum(d["risk"] == "insufficient_evidence" for d in details),
            "failures_or_invalid": sum(not d["valid"] for d in details),
            "forecast_coverage": sum(d["risk"] in {"elevated", "ordinary"} for d in details) / len(details),
            "sensitivity": tp / len(pos), "specificity": tn / len(neg),
            "balanced_accuracy": (tp / len(pos) + tn / len(neg)) / 2,
            "cutoff_state_matches": sum(d["cutoff_state_match"] for d in details),
            "unsupported_fabrication_assertions": sum(d["unsupported_fabrication_assertion"] for d in details),
            **usage(rows)}, details

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reg, corpus = read(HERE / "REGISTRATION.json"), read(HERE / "corpus.json")
    validate_setup(corpus, reg)
    manifest, pred = read(args.run / "manifest.json"), read(args.run / "predictions.json")
    rows = pred["results"]
    if not manifest.get("finished_at") or len(rows) != len(reg["schedule"]):
        raise SystemExit("All scheduled steps must finish before outcome labels are loaded")
    if [{"case_id": r["case_id"], "arm": r["arm"]} for r in rows] != reg["schedule"]:
        raise SystemExit("Schedule mismatch")
    if sha256(args.gold) != reg["sealed_gold_sha256"]:
        raise SystemExit("Sealed outcome key differs from registration")
    gold_doc = read(args.gold)
    gold = {c["id"]: c for c in gold_doc["cases"]}
    cases = {c["id"]: c for c in corpus["cases"]}
    if set(gold) != set(cases) or len(gold_doc["cases"]) != len(cases):
        raise SystemExit("Gold cases differ")
    integrity = {
        "registration_matches_manifest": sha256(HERE / "REGISTRATION.json") == manifest["registration_sha256"],
        "frozen_files_and_cutoff_valid": True,
        "sealed_gold_hash_valid": True,
        "all_scheduled_steps_present_once": True,
        "request_hashes_valid": all(r["request_sha256"] == digest(request_for(cases[r["case_id"]], r["arm"], reg)) for r in rows),
        "identical_packet_hashes_in_both_arms": all(r["packet_sha256"] == digest(cases[r["case_id"]]["packet"]) for r in rows),
        "one_local_call_per_step": all(len(r["calls"]) == 1 and r["calls"][0]["tunnel"] == "local"
                                       and r["calls"][0]["model"] == reg["model"]
                                       and r["calls"][0]["reasoning_effort"] == reg["reasoning_effort"] for r in rows),
        "no_retry_or_fallback": manifest["retries"] == 0 and manifest["fallback"] is False,
        "runner_reports_no_gold_loaded": pred["gold_loaded"] is False and manifest["gold_loaded"] is False,
    }
    metrics, per_case = {}, {}
    for arm in POLICIES:
        metrics[arm], per_case[arm] = score_arm([r for r in rows if r["arm"] == arm], gold, cases)
    direct, harness = metrics["direct"], metrics["harness"]
    d = {r["case_id"]: r for r in per_case["direct"]}
    h = {r["case_id"]: r for r in per_case["harness"]}
    better = sum(h[c]["outcome_match"] and not d[c]["outcome_match"] for c in cases)
    worse = sum(d[c]["outcome_match"] and not h[c]["outcome_match"] for c in cases)
    n = better + worse
    pvalue = min(1.0, 2 * sum(math.comb(n, k) for k in range(min(better, worse) + 1)) / 2**n) if n else 1.0
    delta = harness["balanced_accuracy"] - direct["balanced_accuracy"]
    winner = "harness" if delta > 0 else "direct" if delta < 0 else "tie"
    valid = all(integrity.values()) and all(m["valid_outputs"] == 8 and m["successful_calls"] == 8 for m in metrics.values())
    headline = winner if valid else "invalid/inconclusive (raw scores retained)"
    ratio = harness["total_tokens"] / direct["total_tokens"] if harness["total_tokens"] is not None and direct["total_tokens"] else None
    summary = {"scored_at": now(), "design": "retrospective_same_evidence_decision_stage",
               "model": reg["model"], "reasoning_effort": reg["reasoning_effort"],
               "metrics": metrics, "per_case": per_case, "observed_primary_winner": winner,
               "comparison_status": "valid_exploratory_run" if valid else "invalid_or_incomplete",
               "balanced_accuracy_difference": delta, "all_integrity_checks_passed": all(integrity.values()),
               "all_16_outputs_valid": valid, "observed_candidate_win": winner == "harness" and valid,
               "harness_to_direct_token_ratio": ratio,
               "paired_comparison": {"candidate_only_correct": better, "direct_only_correct": worse, "exact_two_sided_mcnemar_p": pvalue},
               "limitations": ["Eight outcome-aware selected cases, not a blinded representative holdout.",
                               "No automated retrieval, original-image inspection or raw-data verification.",
                               "Right-censored controls are not proved authentic; retraction is not proof of fraud.",
                               "Cutoff-state gold labels are assistant annotations of curated summaries.",
                               "Masking and label sealing cannot remove editorial bias or model memory."]}
    args.output.mkdir(parents=True, exist_ok=False)
    dump(args.output / "SUMMARY.json", summary)
    dump(args.output / "AUDIT.json", integrity)
    dump(args.output / "REVEALED_GOLD.json", gold_doc)
    fmt = lambda v: f"{v:,}" if isinstance(v, int) else "unavailable"
    lines = ["# Astra v6: new dataset with identical evidence", "",
             f"**Observed primary outcome: {headline}.** This is an exploratory decision-stage comparison on eight new papers, not a test of automated source finding.", "",
             "Both arms requested gpt-6-astra at low reasoning through local Codex login (remote inference). Each received the exact same three dated text summaries. The candidate adds the v6 artifact-and-rebuttal policy. One call per case/arm; no retries or fallback.", "",
             "| Measure | Direct Astra | Astra + v6 policy |", "|---|---:|---:|",
             f"| Later-outcome forecast matches | {direct['outcome_matches']}/8 | {harness['outcome_matches']}/8 |",
             f"| Balanced accuracy (abstentions count as misses) | {direct['balanced_accuracy']:.1%} | {harness['balanced_accuracy']:.1%} |",
             f"| Later-retracted cases flagged elevated | {direct['true_positives']}/4 | {harness['true_positives']}/4 |",
             f"| Controls predicted ordinary | {direct['true_negatives']}/4 | {harness['true_negatives']}/4 |",
             f"| Forecast abstentions | {direct['abstentions']} | {harness['abstentions']} |",
             f"| Cutoff record-state annotation matches | {direct['cutoff_state_matches']}/8 | {harness['cutoff_state_matches']}/8 |",
             f"| Unsupported established-fabrication assertions | {direct['unsupported_fabrication_assertions']} | {harness['unsupported_fabrication_assertions']} |",
             f"| Valid outputs | {direct['valid_outputs']}/8 | {harness['valid_outputs']}/8 |",
             f"| Input + output tokens | {fmt(direct['total_tokens'])} | {fmt(harness['total_tokens'])} |", "",
             f"Paired discordances: candidate-only correct {better}; direct-only correct {worse}. Exact two-sided McNemar p = {pvalue:.4f}. Token ratio: {ratio:.3f}x." if ratio is not None else f"Paired p = {pvalue:.4f}; complete token ratio unavailable.", "",
             "## Per-case outcomes", "", "| Case | Later outcome target | Direct forecast | Candidate forecast | Direct cutoff state | Candidate cutoff state |", "|---|---|---|---|---|---|"]
    for cid in cases:
        lines.append(f"| {cid} | {d[cid]['outcome_target']} | {d[cid]['risk']} | {h[cid]['risk']} | {d[cid]['cutoff_state']} | {h[cid]['cutoff_state']} |")
    lines += ["", "## Interpretation and limits", "",
              "Risk forecasts and fact determinations are separate. The hard case with no supplied pre-cutoff concern can be a predictive miss even when abstaining is evidence-responsible. A publisher retraction for unreliable data does not by itself establish deliberate fabrication. No located retraction in a control is not proof that its experiments were authentic.", "",
              "The same outcome-aware assistant selected these cases, paraphrased sources, wrote policies and annotated cutoff states. The key was sealed before calls, but this is not independent blind evaluation. The sample is small, purposely balanced and restricted to scientific articles; original publisher families differ between the two outcome classes. Source dates are reconstructed from current records, not independent historical snapshots. Model training knowledge may still contain later events despite masking.", "",
              "The models read summaries only. These calls do not demonstrate automated source tracing, image manipulation detection, original-data validation, or general fake-news accuracy. Cutoff-state agreement is agreement with declared assistant annotations, not an independently measured truth score.", "",
              "The earlier v5 8/8 versus 4/8 counts came from unequal manually curated evidence packets. Those recorded counts remain unchanged, but interpreting them as proof of automated retrieval or a superior same-evidence policy was unsupported.", "",
              "## Audit artifacts", "",
              "- [Frozen protocol](../../../experiments/astra-same-evidence-v6-20260914/PROTOCOL.md)",
              "- [Source mapping and publication dates](../../../experiments/astra-same-evidence-v6-20260914/SOURCE_AUDIT.json)",
              "- [Complete parsed predictions and call receipts](../run-001/predictions.json)",
              "- [Run manifest](../run-001/manifest.json)",
              "- [Summary](SUMMARY.json), [integrity audit](AUDIT.json), [revealed outcome key](REVEALED_GOLD.json)", "",
              f"Registration SHA-256: `{sha256(HERE / 'REGISTRATION.json')}`. Outcome-key SHA-256: `{sha256(args.gold)}`.",
              f"Pre-call commit: `{manifest['git_commit_before_calls']}`. Integrity checks passed: **{all(integrity.values())}**.", "",
              "Receipts contain parsed model outputs and CLI-reported usage, not full raw provider traces or a server-side checkpoint attestation.", ""]
    (args.output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Observed primary result: {headline}; direct {direct['outcome_matches']}/8; candidate {harness['outcome_matches']}/8; valid={valid}")

if __name__ == "__main__":
    main()
