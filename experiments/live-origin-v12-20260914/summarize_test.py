"""Additional post-run audit: exact primary title excludes correction notices.

Uses an exact article-title match to exclude correction notices. This file is never imported by
the model runner and does not alter any raw response, status, or final validator.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from newsverify.news_client import url_key

# Post-run target identity checks, not model inputs or predictive outcome labels.
PRIMARY = {
    "a701": ("10.1038/s41598-021-97778-3", "A Tunguska sized airburst destroyed Tall el-Hammam a Middle Bronze Age city in the Jordan Valley near the Dead Sea"),
    "a702": ("10.1126/sciadv.abe3647", "Globally distributed iridium layer preserved within the Chicxulub impact structure"),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(run, output):
    marker = json.loads((run / "COMPLETED.json").read_text())
    if digest(run / "predictions.json") != marker["results_sha256"]:
        raise ValueError("Frozen prediction digest mismatch")
    raw = json.loads((run / "predictions.json").read_text())
    rows, stages = [], defaultdict(lambda: {"calls":0,"successful_calls":0,"known_usage_calls":0,
                                          "input_tokens":0,"output_tokens":0})
    for result in raw:
        directory = run / (result["case_id"]+"-"+result["arm"])
        sources = json.loads((directory/"sources.json").read_text())
        calls = json.loads((directory/"calls.json").read_text()) if (directory/"calls.json").exists() else []
        io = json.loads((directory/"model-io.json").read_text()) if (directory/"model-io.json").exists() else []
        if len(calls) != len(io):
            raise ValueError("Attempt count mismatch")
        for call in calls:
            item = stages[result["arm"]+":"+call["stage"]]
            item["calls"] += 1
            item["successful_calls"] += bool(call.get("success"))
            usage = call.get("usage") or {}
            if all(type(usage.get(k)) is int for k in ("input_tokens","output_tokens")):
                item["known_usage_calls"] += 1
                for k in ("input_tokens","output_tokens"):
                    item[k] += usage[k]
        doi,title = PRIMARY[result["case_id"]]
        primary = [s for s in sources if doi.lower() in s["content"].lower()
                   and " ".join(title.casefold().split()) == " ".join(s["title"].split(" | ")[0].casefold().split())]
        native = ((result.get("workflow") or {}).get("results") or [None])[0]
        proposed = result.get("prediction") or {}
        origins = native.get("origin_summary",{}).get("sources",[]) if native else []
        native_primary = any(url_key(o["url"]) == url_key(p["url"]) for o in origins for p in primary)
        strict_native = bool(native and native["status"]=="completed" and not native["errors"]
                             and native_primary and all(not c.get("errors") for c in native["claims"]))
        final_primary = result["status"]=="completed" and any(
            url_key(proposed.get("origin_url")) == url_key(p["url"]) for p in primary)
        usages = [c.get("usage") or {} for c in calls]
        known = [u for u in usages if all(type(u.get(k)) is int for k in ("input_tokens","output_tokens"))]
        trace_quotes = []
        for claim in native["claims"] if native else []:
            trace = claim.get("trace") or {}
            materials = {m["version_id"]:m for m in trace.get("materials",[])}
            for relation in trace.get("relations",[]):
                for span in relation.get("basis",[]):
                    content = materials.get(span["version_id"],{}).get("content","")
                    trace_quotes.append(content[span["start"]:span["end"]]==span["quote"] and bool(span["quote"].strip()))
        rows.append(dict(case_id=result["case_id"],arm=result["arm"],assessment_status=result["status"],
            errors=result["errors"],source_errors=result["source_errors"],
            primary_study_fetched=bool(primary),primary_urls=[p["url"] for p in primary],
            validated_final_primary_chain=bool(final_primary),
            native_status=native["status"] if native else None,native_errors=native["errors"] if native else [],
            native_primary_located=native_primary if native else None,
            strict_native_primary_trace_success=strict_native if native else None,
            validated_primary_chain_and_native_trace=bool(final_primary and strict_native) if native else None,
            native_relation_quote_count=len(trace_quotes),native_relation_quotes_valid=all(trace_quotes),
            fact_status=proposed.get("fact_status"),risk=proposed.get("risk"),
            fabrication_established=proposed.get("fabrication_established"),
            origin_url=proposed.get("origin_url"),origin_chain=proposed.get("origin_chain"),
            rationale=proposed.get("rationale"),
            calls=len(calls),successful_calls=sum(bool(c.get("success")) for c in calls),
            known_usage_calls=len(known),input_tokens=sum(u["input_tokens"] for u in known),
            output_tokens=sum(u["output_tokens"] for u in known),
            total_tokens=sum(u["input_tokens"]+u["output_tokens"] for u in known) if len(known)==len(calls) else None,
            source_count=len(sources),bibliographic_bindings=len(result.get("bibliography",[])),
            sources=[{"url":s["url"],"title":s["title"],"available_at":s["available_at"],
                      "content_sha256":hashlib.sha256(s["content"].encode()).hexdigest(),
                      "characters":len(s["content"])} for s in sources]))
    summary = {}
    for arm in ("direct","harness"):
        selected = [r for r in rows if r["arm"]==arm]
        summary[arm] = {"cases":len(selected), **{k:sum(r[k] for r in selected)
            for k in ("primary_study_fetched","validated_final_primary_chain","calls","successful_calls",
                      "known_usage_calls","input_tokens","output_tokens")}}
        summary[arm]["total_tokens"] = sum(r["total_tokens"] for r in selected) if all(r["total_tokens"] is not None for r in selected) else None
        summary[arm]["strict_native_primary_trace_success"] = sum(bool(r["strict_native_primary_trace_success"]) for r in selected) if arm=="harness" else None
        summary[arm]["validated_primary_chain_and_native_trace"] = sum(bool(r["validated_primary_chain_and_native_trace"]) for r in selected) if arm=="harness" else None
        summary[arm]["full_body_independent_audit_required"] = True
    output.mkdir(parents=True,exist_ok=False)
    receipts = {"created_at":datetime.now(timezone.utc).isoformat(),
                "predictions_sha256":digest(run/"predictions.json"),
                "scope":"Post-run mechanical audit; no model calls or prediction replacement",
                "private_file_hashes":{str(p.relative_to(run)):digest(p) for p in sorted(run.rglob('*')) if p.is_file()}}
    for name,data in (("RESULTS.json",rows),("SUMMARY.json",summary),("STAGE_TOKENS.json",dict(stages)),("RECEIPTS.json",receipts)):
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    audit(Path(sys.argv[1]),Path(sys.argv[2]))
