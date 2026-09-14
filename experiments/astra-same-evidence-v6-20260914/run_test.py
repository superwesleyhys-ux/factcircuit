#!/usr/bin/env python3
"""One fixed local call per arm/case. No outcome labels, retries or retrieval."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from newsverify.tunnels import LocalTunnel
from policies import POLICIES, RESULT_SCHEMA

FROZEN = ["corpus.json", "SOURCE_AUDIT.json", "PROTOCOL.md", "policies.py",
          "run_test.py", "summarize_test.py", "check_safeguards.py",
          "../../newsverify/tunnels.py", "../../newsverify/__init__.py"]

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def dump(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)

def now():
    return datetime.now(timezone.utc).isoformat()

def validate_setup(corpus, registration):
    if {name: sha256(HERE / name) for name in FROZEN} != registration["frozen_sha256"]:
        raise ValueError("frozen files changed")
    cases = corpus["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != 8 or len(set(ids)) != 8 or ids != registration["case_order"]:
        raise ValueError("case order/uniqueness mismatch")
    expected = [(cid, arm) for cid in ids for arm in POLICIES]
    schedule = [(s["case_id"], s["arm"]) for s in registration["schedule"]]
    if len(schedule) != 16 or sorted(schedule) != sorted(expected):
        raise ValueError("schedule must include each arm/case exactly once")
    for case in cases:
        if set(case) != {"id", "packet"}:
            raise ValueError("only one shared evidence packet is permitted")
        packet = case["packet"]
        if set(packet) != {"case_id", "cutoff", "question", "evidence_passages"}:
            raise ValueError("unexpected packet fields")
        if packet["case_id"] != case["id"] or packet["cutoff"] != registration["cutoff"]:
            raise ValueError("packet identity/cutoff mismatch")
        if [p["id"] for p in packet["evidence_passages"]] != ["p001", "p002", "p003"]:
            raise ValueError("unexpected passage IDs")
        for passage in packet["evidence_passages"]:
            if set(passage) != {"id", "available_at", "kind", "text"}:
                raise ValueError("unexpected passage fields")
            if not ("1900-01-01" <= passage["available_at"] <= registration["cutoff"]):
                raise ValueError("post-cutoff passage")
        if any(x in json.dumps(packet).lower() for x in ["https://", "doi.org", "later_outcome", "target_label"]):
            raise ValueError("unmasked identifier or outcome field")

def request_for(case, arm, registration):
    return {"model": registration["model"], "reasoning_effort": registration["reasoning_effort"],
            "instructions": POLICIES[arm], "packet": case["packet"], "schema": RESULT_SCHEMA}

def validate_response(value, packet):
    if not isinstance(value, dict) or set(value) != set(RESULT_SCHEMA["properties"]):
        raise ValueError("response fields differ from schema")
    for field in ("risk", "cutoff_assessment", "confidence"):
        if value[field] not in RESULT_SCHEMA["properties"][field]["enum"]:
            raise ValueError("invalid categorical output")
    if type(value["fabrication_established"]) is not bool:
        raise ValueError("fabrication flag must be boolean")
    refs = value["evidence_passage_ids"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 3 or any(not isinstance(x, str) for x in refs):
        raise ValueError("invalid citation list")
    if len(set(refs)) != len(refs) or not set(refs) <= {p["id"] for p in packet["evidence_passages"]}:
        raise ValueError("invalid or duplicate citation")
    if not isinstance(value["rationale"], str) or not 1 <= len(value["rationale"].split()) <= 180:
        raise ValueError("rationale must contain 1 to 180 words")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    corpus, registration = read(HERE / "corpus.json"), read(HERE / "REGISTRATION.json")
    validate_setup(corpus, registration)
    if args.preflight_only:
        print("Frozen files, single shared packets, dates and 16-call schedule validated; no model calls.")
        return
    if args.output is None:
        parser.error("--output is required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "started_at": now(), "registration_sha256": sha256(HERE / "REGISTRATION.json"),
        "git_commit_before_calls": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "codex_cli_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "requested_model": registration["model"], "reasoning_effort": registration["reasoning_effort"],
        "tunnel": "local", "model_weights_local": False, "api_credentials_removed": True,
        "web_tools_plugins_skills_disabled": True, "retrieval_performed": False,
        "retries": 0, "fallback": False, "gold_loaded": False,
        "calls_expected": 16, "timeout_seconds": registration["timeout_seconds"],
    }
    dump(output / "manifest.json", manifest)
    cases = {c["id"]: c for c in corpus["cases"]}
    rows = []
    for sequence, step in enumerate(registration["schedule"], 1):
        cid, arm = step["case_id"], step["arm"]
        request = request_for(cases[cid], arm, registration)
        row = {"sequence": sequence, "case_id": cid, "arm": arm, "started_at": now(),
               "status": "running", "request_sha256": digest(request),
               "packet_sha256": digest(request["packet"]), "result": None, "calls": []}
        rows.append(row)
        dump(output / "predictions.json", {"gold_loaded": False, "results": rows})
        transport = LocalTunnel(model=registration["model"], reasoning_effort=registration["reasoning_effort"],
                                timeout=registration["timeout_seconds"])
        try:
            value = transport.generate(stage=f"astra_v6_{arm}", instructions=request["instructions"],
                                       packet=request["packet"], schema=request["schema"])
            row["result"] = value  # Retain schema-invalid parsed responses too.
            validate_response(value, request["packet"])
        except Exception as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        else:
            row["status"] = "completed"
        row.update(calls=transport.calls, finished_at=now())
        dump(output / "predictions.json", {"gold_loaded": False, "results": rows})
        print(json.dumps({"step": sequence, "case_id": cid, "arm": arm, "status": row["status"]}), flush=True)
    manifest.update(finished_at=now(), calls_attempted=sum(len(r["calls"]) for r in rows),
                    outputs_valid=sum(r["status"] == "completed" for r in rows))
    dump(output / "manifest.json", manifest)

if __name__ == "__main__":
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("CODEX_API_KEY", None)
    main()
