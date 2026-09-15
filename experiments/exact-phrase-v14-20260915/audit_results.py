"""Audit exact packets/citations and summarize usage; never change predictions."""
from pathlib import Path
import argparse
import json
import sys

from run_test import HERE, ROOT, MODEL, EFFORT, verify, sha
from newsverify.phrase_spans import text_sha256, validate_span
from newsverify.tunnels import _prompt_parts


def audit(directory):
    registration = json.loads((HERE / "REGISTRATION.json").read_text())
    verify(registration)
    reports, inputs, checks = {}, {}, []
    def check(name, passed):
        checks.append(dict(name=name, passed=bool(passed)))
    for arm in ("direct", "harness"):
        result = json.loads((directory / (arm + ".json")).read_text())
        requests = [json.loads(p.read_text()) for p in sorted((directory / arm / "cli").glob("*.request.json"))]
        stdout = sorted((directory / arm / "cli").glob("*.stdout"))
        ios = [io for item in result["items"] for io in item["model_io"]]
        calls = result["calls"]
        check(arm + "_call_counts", len(requests) == len(stdout) == len(ios) == len(calls) == result["model_calls"])
        check(arm + "_planned_item_count", len(result["items"]) == registration["planned_items"])
        input_tokens = output_tokens = cached = 0
        for index, (request, io, call, logpath) in enumerate(zip(requests, ios, calls, stdout)):
            prefix = f"{arm}_{index+1}"
            instructions, evidence = _prompt_parts(io["instructions"], io["packet"])
            expected = instructions + "\n" + evidence
            check(prefix + "_exact_stdin", request["stdin"] == expected and text_sha256(expected) == request["stdin_sha256"] == call["input_sha256"])
            check(prefix + "_schema", request["schema"] == io["schema"])
            check(prefix + "_model_route", request["model"] == call["model"] == MODEL and request["reasoning_effort"] == call["reasoning_effort"] == EFFORT and call["tunnel"] == "local")
            check(prefix + "_policy", text_sha256(io["instructions"]) == registration["policy_sha256"][arm])
            events = [json.loads(line) for line in logpath.read_text().splitlines() if line.strip()]
            turns = [e for e in events if e.get("type") == "turn.completed"]
            messages = [e["item"]["text"] for e in events if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
            check(prefix + "_one_completed_turn", len(turns) == 1 and call["success"] and call["exit_code"] == 0)
            check(prefix + "_raw_response", bool(messages) and json.loads(messages[-1]) == io["response"])
            check(prefix + "_no_extra_tools", all(e.get("item", {}).get("type") in {"agent_message", "reasoning"} for e in events if e.get("type", "").startswith("item.")))
            usage = call["usage"]
            check(prefix + "_usage", bool(turns) and all(usage.get(key) == turns[0]["usage"].get(key) for key in ("input_tokens", "output_tokens")))
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            cached += usage.get("cached_input_tokens") or 0
            for doc in io["packet"]["documents"]:
                check(prefix + "_unchanged_" + doc["source_id"], text_sha256(doc["content"]) == doc["text_sha256"])
            check(prefix + "_no_summary_fields", not any(key in io["packet"] for key in ("summary", "research_advice", "prior_judgment", "synthesis")))
        inputs[arm] = [item["model_io"][0]["packet"] for item in result["items"]]
        rows = []
        for item in result["items"]:
            selection = item["item"]
            seed = result["input_source"]
            validate_span(seed["content"], selection["start"], selection["end"], selection["text"])
            check(arm + "_context_" + selection["id"], selection["before"]["text"] + selection["text"] + selection["after"]["text"] == selection["context"]["text"])
            for citation in item["citations"]:
                doc = next(d for d in item["documents"] if d["source_id"] == citation["source_id"])
                validate_span(doc["content"], citation["start"], citation["end"], citation["text"])
            check(arm + "_per_item_budget_" + selection["id"], len(item["model_io"]) <= result["limits"]["max_calls"])
            rows.append(dict(item=selection["text"], start=selection["start"], end=selection["end"],
                             status=item["status"], calls=len(item["model_io"]), errors=item["errors"],
                             judgment=item.get("judgment"), citation_count=len(item["citations"]),
                             read_source_urls=[d["url"] for d in item["documents"]]))
        reports[arm] = dict(status=result["status"], calls=len(calls), input_tokens=input_tokens, output_tokens=output_tokens,
                            total_tokens=input_tokens+output_tokens, cached_input_tokens=cached, items=rows,
                            result_sha256=sha(directory / (arm + ".json")))
    check("identical_first_packets_all_items", inputs["direct"] == inputs["harness"])
    return dict(checks=checks, all_checks_passed=all(c["passed"] for c in checks), arms=reports,
                registration_sha256=sha(HERE / "REGISTRATION.json"), frozen_files_match=True,
                scope="Packet/citation and receipt integrity, not proof of world truth or model accuracy")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.directory)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"all_checks_passed": report["all_checks_passed"], "checks": len(report["checks"]),
                      "arms": {k: {f: v[f] for f in ("status", "calls", "total_tokens")} for k, v in report["arms"].items()}}))
    sys.exit(0 if report["all_checks_passed"] else 1)
