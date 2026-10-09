"""Bounded semantic stages with source-backed review and atomic assembly.

The public engine protocols stay unchanged: this module still returns
``Analysis`` and ``VerificationResult``. Internally, each model prompt has one
responsibility and every accepted result passes deterministic Python gates.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
from urllib.parse import urlsplit

from factcircuit import provenance as p

try:  # Experiment scripts place this directory directly on sys.path.
    from prompt_specs import (
        ATOMS_PROMPT, ATOMS_SCHEMA, DECOMPOSITION_CRITIC_PROMPT,
        DECOMPOSITION_CRITIC_SCHEMA, EVIDENCE_PROMPT,
        EVIDENCE_CRITIC_PROMPT, LAYER_CRITIC_SCHEMA, LAYER_SCHEMA,
        LINEAGE_PROMPT, LINEAGE_SCHEMA, PROMPT_VERSION, WORLD_CRITIC_PROMPT,
        WORLD_PROMPT,
    )
    from target_plan import (build_target_plan, dimension_evidence_is_grounded,
                             dimension_signal_is_grounded, evidence_terms)
except ModuleNotFoundError:  # Also support namespace-package imports in tests.
    from experiments.prompt_specs import (
        ATOMS_PROMPT, ATOMS_SCHEMA, DECOMPOSITION_CRITIC_PROMPT,
        DECOMPOSITION_CRITIC_SCHEMA, EVIDENCE_PROMPT,
        EVIDENCE_CRITIC_PROMPT, LAYER_CRITIC_SCHEMA, LAYER_SCHEMA,
        LINEAGE_PROMPT, LINEAGE_SCHEMA, PROMPT_VERSION, WORLD_CRITIC_PROMPT,
        WORLD_PROMPT,
    )
    from experiments.target_plan import (
        build_target_plan, dimension_evidence_is_grounded,
        dimension_signal_is_grounded, evidence_terms)


class StagedSemanticError(ValueError):
    """A stage failed its explicit output or review contract."""

    def __init__(self, stage, reason, repairable=False):
        self.stage = stage
        self.repairable = repairable
        super().__init__(f"staged semantic {stage}: {reason}")


STAGE_OUTPUT_CAPS = {
    "atoms": 2500,
    "lineage": 2500,
    "decomposition_critic": 1000,
    "evidence": 8000,
    "evidence_critic": 1500,
    "world": 8000,
    "world_critic": 1500,
}
MAX_STAGE_INPUT_CHARS = 250_000
MAX_ADJACENT_GAP_CHARS = 128

_GATE_REPAIR_ISSUES = {
    "probe_coverage": "Return every immutable target probe exactly once.",
    "dimension_coverage": "Return every required dimension for the named probe exactly once.",
    "dimension_basis_index": "Use unique in-range basis indices for the named dimension.",
    "dimension_basis_or_scope": "Keep the dimension unresolved unless exact basis and complete scope are present.",
    "dimension_grounding": "Use exact basis that grounds the named probe dimension.",
    "stop_task_exclusivity": (
        "Choose concrete non-program tasks with stop_reason=none, or an explicit "
        "stop with no such tasks."),
    "structure_gate": "Correct the deterministic structure or grounding contract.",
}


def _gate_repair(exc, previous_draft):
    """Turn only program-owned gate codes into a directed repair payload."""
    parts = str(exc).split(":")
    code = parts[0] if parts[0] in _GATE_REPAIR_ISSUES else "structure_gate"
    probe_number = (int(parts[1]) if len(parts) > 1 and parts[1].isdigit()
                    else None)
    dimension = parts[2] if len(parts) > 2 else None
    return {
        "error_code": code,
        "probe_number": probe_number,
        "dimension": dimension,
        "issue": _GATE_REPAIR_ISSUES[code],
        "basis": [],
        "previous_draft": previous_draft,
    }


def _validate(value, spec):
    """Validate every wire field and bound before translating model output."""
    if "anyOf" in spec:
        for option in spec["anyOf"]:
            try:
                _validate(value, option)
                return
            except ValueError:
                pass
        raise ValueError("invalid union value")
    kind = spec["type"]
    expected = {"object": dict, "array": list, "string": str,
                "integer": int, "boolean": bool, "null": type(None)}[kind]
    if type(value) is not expected:
        raise ValueError("invalid JSON field type")
    if kind == "object":
        if set(value) != set(spec["properties"]):
            raise ValueError("missing or unexpected JSON fields")
        for key, child in spec["properties"].items():
            _validate(value[key], child)
    elif kind == "array":
        if not spec.get("minItems", 0) <= len(value) <= spec["maxItems"]:
            raise ValueError("too many stage items")
        for item in value:
            _validate(item, spec["items"])
    elif kind == "string":
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", len(value)):
            raise ValueError("invalid string length")
        if spec.get("minLength", 0) and not value.strip():
            raise ValueError("empty string")
    elif kind == "integer":
        if not spec.get("minimum", value) <= value <= spec.get("maximum", value):
            raise ValueError("integer outside stage bounds")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("invalid enumeration")


def _id(owner, kind, value):
    digest = hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    return f"{owner}:{kind}:{digest}"


def _span(material, quote):
    content = material["content"]
    start = content.find(quote)
    if not quote or start < 0 or content.find(quote, start + 1) >= 0:
        raise ValueError("source quote is absent or not unique")
    return p.Span(material["version_id"], start, start + len(quote), quote)


def _qualifier_span(material, parent_quote, quote):
    parent = _span(material, parent_quote)
    offset = parent_quote.find(quote)
    if not quote or offset < 0 or parent_quote.find(quote, offset + 1) >= 0:
        raise ValueError("qualifier must occur uniquely inside its parent atom quote")
    return p.Span(parent.version_id, parent.start + offset,
                  parent.start + offset + len(quote), quote)


def _registry(context, target, active=True):
    """Project active tasks for models or historical IDs for Python closure."""
    result = {}
    source = context.get("gaps", []) if active else context.get("gap_registry", [])
    for gap in source:
        if gap.get("target_id") in (None, target.id):
            if gap["id"] in result and result[gap["id"]] != gap:
                raise ValueError("conflicting registered gap")
            result[gap["id"]] = gap
    return result


def _dimension(gap):
    value = gap.get("dimension", "auto")
    return ("provenance" if gap["stage"] == "provenance" else "world") if value == "auto" else value


def _is_program_scope_gap(target, dimension, gap_id, gap):
    """Identify the one target-level task whose lifecycle the engine owns."""
    locator = gap.get("locator")
    return (dimension == "evidence"
            and gap.get("stage") == "verification"
            and _dimension(gap) == "evidence"
            and gap.get("action") == "fetch"
            and gap.get("probe_id") is None
            and locator in target.evidence_scope
            and gap_id == _id(target.id, "missing-scope", locator))


def _feedback(context, target, material):
    receipt = context.get("current_return", {})
    task_ids = receipt.get("task_ids", [])
    registry = {gap["id"]: gap for gap in context.get("gap_registry", [])}
    if task_ids:
        active = [registry[item] for item in task_ids
                  if item in registry
                  and registry[item].get("target_id") in (None, target.id)
                  and registry[item].get("stage") == "verification"]
        attribution = "exact"
    else:
        # Compatibility only for providers that predate RetrievalHit.
        locators = {material.version_id, material.url}
        active = [gap for gap in context.get("gaps", [])
                  if gap.get("target_id") in (None, target.id)
                  and gap.get("stage") == "verification"
                  and (gap.get("locator") in locators or any(
                      ref["version_id"] == material.version_id
                      for ref in gap.get("basis", [])))]
        attribution = "legacy_heuristic" if active else "unattributed"
    last = context.get("verification_history", [])[-1:]
    return {
        "target_id": target.id,
        "tasks": active,
        "retrieval_receipt": receipt,
        "attribution": attribution,
        "last_assessment": [{key: item.get(key) for key in (
            "evidence_verdict", "world_verdict", "rationale", "world_rationale")}
            for item in last],
    }


def _canonical_fragment(item):
    """Only exact, verifier-consumed fields participate in staged judgement."""
    return {"parent_id": item["parent_id"], "span": item["span"],
            "qualifier_spans": item.get("qualifier_spans", [])}


def _canonical_relation(item):
    return {key: item.get(key) for key in (
        "from_version", "to_version", "kind", "status", "basis",
        "upstream_locator")}


def _canonical_origin(item):
    return {key: item.get(key) for key in (
        "target_id", "version_id", "basis", "material_kind")}


def _canonical_material(item):
    return {key: value for key, value in item.items() if key != "retrieved_at"}


class _StageClient:
    def __init__(self, client):
        self.client = client
        self.history = []
        self.transaction_sequence = 0
        self.current_transaction = None

    def _begin_transaction(self, kind, target_id, material=None):
        self.transaction_sequence += 1
        self.current_transaction = (
            f"{kind}:{target_id}:{material or 'all'}:{self.transaction_sequence}")

    def _assembly_audit(self, stage, material, context, inputs, output=None,
                        error_code=None):
        def dataclass_default(item):
            if is_dataclass(item):
                return asdict(item)
            raise TypeError("assembly audit input is not JSON serializable")

        def encode(value):
            return json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                default=dataclass_default)

        encoded_inputs = encode(inputs)
        record = {
            "sequence": len(self.history) + 1,
            "material": material,
            "round": context.get("usage", {}).get("rounds", 0),
            "stage": stage,
            "repair": 0,
            "prompt_version": PROMPT_VERSION,
            "input_sha256": hashlib.sha256(encoded_inputs.encode()).hexdigest(),
            "status": "accepted" if error_code is None else "failed",
            "transaction_id": self.current_transaction,
        }
        if output is not None:
            encoded_output = encode(output)
            record["output_sha256"] = hashlib.sha256(
                encoded_output.encode()).hexdigest()
        if error_code is not None:
            record["error_code"] = error_code
        self.history.append(record)

    def _reserve(self, calls, output_tokens):
        reserve = getattr(self.client, "reserve", None)
        if reserve is not None:
            try:
                reserve(calls, output_tokens)
            except Exception as exc:
                self.history.append({
                    "sequence": len(self.history) + 1,
                    "material": None,
                    "round": 0,
                    "stage": "transaction_reservation",
                    "repair": 0,
                    "prompt_version": PROMPT_VERSION,
                    "status": "failed",
                    "error_code": getattr(exc, "code", "budget_exhausted"),
                    "reserved_calls": calls,
                    "reserved_output_tokens": output_tokens,
                    "transaction_id": self.current_transaction,
                })
                raise StagedSemanticError(
                    "transaction_reservation", "resource budget exhausted before transaction") from None

    def _attach_model_record(self, audit, previous_count):
        records = getattr(self.client, "records", ())
        if len(records) <= previous_count:
            return
        record = records[-1]
        audit["model_call_sequence"] = record.get("sequence")
        audit["request_digest"] = record.get("request_digest")
        for key in ("response_id", "usage", "cached_from", "actual_model",
                    "requested_model"):
            if key in record:
                audit[key] = record[key]

    def _call(self, stage, prompt, payload, spec, material, context, repair=0):
        serialized = json.dumps(payload, ensure_ascii=False,
                                sort_keys=True, separators=(",", ":"))
        audit = {
            "sequence": len(self.history) + 1,
            "material": material,
            "round": context.get("usage", {}).get("rounds", 0),
            "stage": stage,
            "repair": repair,
            "prompt_version": PROMPT_VERSION,
            "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "payload_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "schema_sha256": hashlib.sha256(json.dumps(
                spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "status": "started",
            "transaction_id": self.current_transaction,
        }
        if repair:
            audit["repair_of"] = next((item["sequence"] for item in reversed(self.history)
                                       if item.get("stage") == stage), None)
        self.history.append(audit)
        if len(serialized) > MAX_STAGE_INPUT_CHARS:
            audit.update(status="failed", error_code="stage_input_too_large")
            raise StagedSemanticError(stage, "bounded stage input exceeded")
        previous_record_count = len(getattr(self.client, "records", ()))
        try:
            raw = self.client.call(
                prompt, serialized, spec,
                stage=stage, prompt_version=PROMPT_VERSION,
                max_output_tokens=STAGE_OUTPUT_CAPS[stage])
            self._attach_model_record(audit, previous_record_count)
            encoded_output = json.dumps(
                raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            audit["output_sha256"] = hashlib.sha256(
                encoded_output.encode()).hexdigest()
            _validate(raw, spec)
        except Exception as exc:
            if "model_call_sequence" not in audit:
                self._attach_model_record(audit, previous_record_count)
            audit["status"] = "failed"
            audit["error_code"] = getattr(exc, "code", "stage_request_or_schema")
            # Do not persist arbitrary transport text or model output here.
            raise StagedSemanticError(
                stage, "request or output validation failed (" + type(exc).__name__ + ")") from None
        audit["status"] = "validated"
        return raw


class StagedDecomposer(_StageClient):
    """atoms -> lineage -> critic, with at most one directed repair."""

    def __init__(self, client, max_repairs=1):
        super().__init__(client)
        if type(max_repairs) is not int or max_repairs not in (0, 1):
            raise ValueError("max_repairs must be 0 or 1")
        self.max_repairs = max_repairs
        self.plans = {}

    requires_task_attribution = True

    def decompose(self, target, material, context):
        self._begin_transaction("decompose", target.id, material.version_id)
        self._reserve(3 + 2 * self.max_repairs,
                      6000 + 3500 * self.max_repairs)
        source = _canonical_material(asdict(material))
        visible = {item["version_id"]: item for item in context.get("materials", [])}
        visible[material.version_id] = source
        plan = build_target_plan(target)
        previous_plan = self.plans.setdefault(target.id, plan)
        if previous_plan != plan:
            raise StagedSemanticError("target_plan", "immutable target plan changed")
        previous = context.get("analyses", {}).get(material.version_id) or {}
        receipt = context.get("current_return", {})
        common = {
            "target": asdict(target),
            "target_plan": plan,
            "material": source,
            "retrieval_receipt": receipt,
        }
        stage_payloads = {
            "atoms": {
                **common,
                "previous_atoms": previous.get("fragments", []),
            },
            "lineage": {
                **common,
                "previous_lineage": {key: previous.get(key, []) for key in (
                    "relations", "gaps", "resolutions", "origins", "revisit_versions")},
            "available_sources": [{"version_id": item["version_id"], "url": item["url"]}
                                  for item in visible.values()],
                "known_relations": [_canonical_relation(item)
                                    for item in context.get("relations", [])],
            },
        }
        drafts = {}

        def run(stage, repair=None, number=0):
            prompt, spec = ((ATOMS_PROMPT, ATOMS_SCHEMA) if stage == "atoms"
                            else (LINEAGE_PROMPT, LINEAGE_SCHEMA))
            repair_payload = None if repair is None else {
                "instruction": repair,
                "previous_draft": drafts.get(stage),
            }
            raw = self._call(stage, prompt,
                             {**stage_payloads[stage], "repair": repair_payload}, spec,
                             material.version_id, context, number)
            drafts[stage] = raw
            try:
                self._check_draft(stage, raw, source, visible)
            except ValueError as exc:
                self.history[-1]["status"] = "failed"
                raise StagedSemanticError(stage, str(exc), repairable=True) from None
            self.history[-1]["status"] = "accepted"

        repairs_used = 0
        for stage in ("atoms", "lineage"):
            try:
                run(stage)
            except StagedSemanticError as exc:
                if not exc.repairable or repairs_used >= self.max_repairs:
                    raise
                repairs_used += 1
                run(stage, {"quote": "",
                            "issue": "Correct the deterministic quote, uniqueness, or dependency gate."},
                    repairs_used)
        critic_round = 0
        while True:
            critic = self._call(
                "decomposition_critic", DECOMPOSITION_CRITIC_PROMPT,
                {**common, "previous_atoms": previous.get("fragments", []),
                 "previous_lineage": {key: previous.get(key, []) for key in (
                     "relations", "gaps", "resolutions", "origins", "revisit_versions")},
                 "drafts": drafts,
                 "verifier_feedback": _feedback(context, target, material)},
                DECOMPOSITION_CRITIC_SCHEMA, material.version_id, context,
                critic_round)
            if critic["decision"] == "accept":
                if critic["stage"] != "none" or critic["quote"] or critic["issue"]:
                    self.history[-1]["status"] = "failed"
                    raise StagedSemanticError(
                        "decomposition_critic", "accept must have no repair instruction")
                self.history[-1]["status"] = "accepted"
                break
            try:
                if critic["stage"] == "none" or not critic["issue"].strip():
                    raise ValueError("repair or rejection needs a stage and issue")
                _span(source, critic["quote"])
            except ValueError as exc:
                self.history[-1]["status"] = "failed"
                raise StagedSemanticError("decomposition_critic", str(exc)) from None
            if critic["decision"] == "reject" or repairs_used >= self.max_repairs:
                self.history[-1]["status"] = (
                    "rejected" if critic["decision"] == "reject" else "repair_exhausted")
                reason = "draft rejected" if critic["decision"] == "reject" else "repair budget exhausted"
                raise StagedSemanticError("decomposition_critic", reason)
            self.history[-1]["status"] = "repair_requested"
            repairs_used += 1
            run(critic["stage"], {"quote": critic["quote"],
                                  "issue": critic["issue"]}, repairs_used)
            critic_round += 1

        # Bind every value consumed by deterministic assembly, rather than
        # only the two model drafts. This makes changes in source visibility,
        # graph state, task lifecycle, or target identity auditable even when
        # the model happens to return byte-identical drafts.
        assembly_inputs = {
            "target": asdict(target),
            "source": source,
            "visible": visible,
            "context": context,
            "registry": _registry(context, target, active=False),
            "target_plan": plan,
            "drafts": drafts,
        }
        try:
            result = self._assemble(target, source, visible, context, drafts)
        except ValueError as exc:
            self._assembly_audit(
                "decomposition_assembly", material.version_id, context,
                assembly_inputs, error_code="deterministic_assembly")
            raise StagedSemanticError("assembly", str(exc)) from None
        self._assembly_audit(
            "decomposition_assembly", material.version_id, context,
            assembly_inputs, asdict(result))
        return result

    @staticmethod
    def _check_draft(stage, raw, source, visible):
        seen = set()
        for item in raw["atoms" if stage == "atoms" else "citations"]:
            _span(source, item["quote"])
            if stage == "atoms":
                key = (item["quote"], item["statement"])
                if len(set(item["qualifier_quotes"])) != len(item["qualifier_quotes"]):
                    raise ValueError("duplicate qualifier quotes")
                for quote in item["qualifier_quotes"]:
                    _qualifier_span(source, item["quote"], quote)
            else:
                key = (item["locator"], item["kind"])
                if item["locator"] not in item["quote"]:
                    raise ValueError("citation locator lacks source-side evidence")
                matches = [candidate for candidate in visible.values()
                           if item["locator"] in (candidate["url"], candidate["version_id"])]
                if matches and matches[0]["version_id"] == source["version_id"]:
                    if len(matches) == 1:
                        raise ValueError("citation cannot identify the current material as upstream")
            if key in seen:
                raise ValueError("duplicate stage finding")
            seen.add(key)
        if stage == "lineage" and raw["origin"] is not None:
            _span(source, raw["origin"]["quote"])
        if stage == "lineage":
            revisit_ids = set()
            for item in raw["revisit"]:
                if (item["version_id"] == source["version_id"]
                        or item["version_id"] not in visible):
                    raise ValueError("revisit must name another visible version")
                if item["version_id"] in revisit_ids:
                    raise ValueError("duplicate revisit version")
                revisit_ids.add(item["version_id"])
                _span(source, item["quote"])

    @staticmethod
    def _assemble(target, source, visible, context, drafts):
        owner = source["version_id"]
        fragments = []
        for item in drafts["atoms"]["atoms"]:
            qualifiers = tuple(sorted(
                (_qualifier_span(source, item["quote"], quote)
                 for quote in item["qualifier_quotes"]),
                key=lambda span: (span.start, span.end)))
            fragments.append(p.Fragment(
                _id(owner, "atom", [item["quote"], item["statement"]]),
                item["statement"], _span(source, item["quote"]), target.id,
                tuple(span.quote for span in qualifiers), qualifiers))

        relations, resolutions, pending_gaps = [], {}, {}
        registry = _registry(context, target, active=False)
        for item in drafts["lineage"]["citations"]:
            locator = item["locator"]
            matches = [candidate["version_id"] for candidate in visible.values()
                       if locator in (candidate["url"], candidate["version_id"])]
            upstream = matches[0] if len(matches) == 1 and matches[0] != owner else None
            basis = (_span(source, item["quote"]),)
            relations.append(p.Relation(
                _id(owner, "citation", [locator, item["kind"]]), owner, upstream,
                item["kind"], "direct" if upstream else "declared", basis,
                item["rationale"], locator))
            if upstream is None:
                parsed = urlsplit(locator)
                action = "fetch" if parsed.scheme in ("http", "https") and parsed.netloc else "search"
                key = (action, locator)
                entry = pending_gaps.setdefault(key, {"basis": [], "impact": []})
                entry["basis"].extend(basis)
                entry["impact"].append(item["decision_impact"])
            else:
                for gap in registry.values():
                    if gap["stage"] == "provenance" and gap.get("locator") == locator:
                        resolutions[gap["id"]] = p.Resolution(
                            gap["id"], basis,
                            "The source-side citation now identifies a visible upstream version.")

        gaps = []
        for (action, locator), entry in sorted(pending_gaps.items()):
            support = tuple(sorted(set(entry["basis"]),
                                   key=lambda span: (span.start, span.end, span.quote)))
            impact = " | ".join(sorted(set(entry["impact"])))
            gaps.append(p.Gap(
                _id(owner, "upstream", [target.id, action, locator]),
                "Locate and disambiguate the explicitly cited upstream: " + locator,
                stage="provenance", dimension="provenance", blocking=True,
                target_id=target.id, basis=support,
                decision_impact=impact, action=action, locator=locator))

        origin = drafts["lineage"]["origin"]
        origins = (() if origin is None else (p.OriginFinding(
            target.id, owner, (_span(source, origin["quote"]),),
            origin["kind"], origin["rationale"]),))

        graph_edges = ([relation for relation in context.get("relations", [])
                        if relation["from_version"] != owner] +
                       [asdict(relation) for relation in relations])
        graph_origins = ([finding for finding in context.get("origins", [])
                          if finding["version_id"] != owner] +
                         [asdict(finding) for finding in origins])
        roots = {finding["version_id"] for finding in graph_origins
                 if finding["target_id"] == target.id and finding["version_id"] in visible}
        pending = [(target.source_version_id, ())]
        visited = set()
        while pending:
            version, path = pending.pop(0)
            if version in visited or version not in visible:
                continue
            visited.add(version)
            if version in roots:
                basis = (origins[0].basis if version == owner and origins else next(
                    (relation.basis for relation in relations if relation.id in path), ()))
                if basis:
                    for gap_id in ("origin:" + target.id, "lineage:" + target.id):
                        if gap_id in registry and registry[gap_id]["stage"] == "provenance":
                            resolutions[gap_id] = p.Resolution(
                                gap_id, basis,
                                "Rechecked findings establish a path from the target source to an original material.")
                break
            for edge in sorted(graph_edges, key=lambda relation: relation["id"]):
                if (edge["from_version"] == version and edge["status"] == "direct"
                        and edge["kind"] in {"quotes", "cites", "reprints", "translates", "derives"}):
                    pending.append((edge["to_version"], path + (edge["id"],)))

        revisit = {item["version_id"] for item in drafts["lineage"]["revisit"]}
        receipt_ids = set(context.get("current_return", {}).get("task_ids", []))
        for gap_id in receipt_ids:
            gap = registry.get(gap_id)
            if gap is not None:
                revisit.update(ref["version_id"] for ref in gap.get("basis", [])
                               if ref["version_id"] in visible
                               and ref["version_id"] != owner)

        return p.Analysis(
            fragments=tuple(sorted(fragments, key=lambda item: item.id)),
            relations=tuple(sorted(relations, key=lambda item: item.id)),
            gaps=tuple(sorted(gaps, key=lambda item: item.id)),
            resolutions=tuple(resolutions[key] for key in sorted(resolutions)),
            origins=origins,
            notes="\n".join(drafts[key]["notes"] for key in ("atoms", "lineage")
                            if drafts[key]["notes"]),
            revisit_versions=tuple(sorted(revisit)),
        )


class StagedVerifier(_StageClient):
    """Per-probe evidence/world transactions with isolated critics."""

    uses_retrieval_feedback = True

    def __init__(self, client, max_repairs=1):
        super().__init__(client)
        if type(max_repairs) is not int or max_repairs not in (0, 1):
            raise ValueError("max_repairs must be 0 or 1")
        self.max_repairs = max_repairs
        self.plans = {}
        self.layer_cache = {"evidence": {}, "world": {}}

    def verify(self, target, context):
        self._begin_transaction("verify", target.id)
        visible = {material["version_id"]: material
                   for material in context.get("materials", [])}
        registry = _registry(context, target)
        plan = build_target_plan(target)
        previous_plan = self.plans.setdefault(target.id, plan)
        if previous_plan != plan:
            raise StagedSemanticError("target_plan", "immutable target plan changed")

        def layer_payload(dimension):
            allowed = {key: material for key, material in visible.items()
                       if dimension == "world" or not target.evidence_scope
                       or key in target.evidence_scope}
            registered = {key: gap for key, gap in registry.items()
                          if gap["stage"] == "verification"
                          and _dimension(gap) == dimension}
            missing_scope = ([key for key in target.evidence_scope if key not in visible]
                             if dimension == "evidence" else [])
            receipts = []
            for receipt in context.get("current_round_returns", []):
                if receipt.get("version_id") not in allowed:
                    continue
                matching_ids = [gap_id for gap_id in receipt.get("task_ids", [])
                                if gap_id in registered]
                if not matching_ids:
                    continue
                matching_tasks = [task for task in receipt.get("tasks", [])
                                  if task.get("id") in matching_ids]
                receipts.append({
                    "version_id": receipt["version_id"],
                    "task_ids": matching_ids,
                    "tasks": matching_tasks,
                    "attribution": receipt.get("attribution"),
                })
            program_scope = {
                gap_id: gap for gap_id, gap in registered.items()
                if _is_program_scope_gap(target, dimension, gap_id, gap)
            }
            model_registered = [
                gap for gap_id, gap in registered.items()
                if gap_id not in program_scope
            ]
            returned_pairs = {
                (gap_id, receipt["version_id"])
                for receipt in receipts
                for gap_id in receipt.get("task_ids", [])
            }
            payload = {
                "target": asdict(target),
                "target_plan": plan,
                "materials": [_canonical_material(item)
                              for item in allowed.values()],
                "missing_scope": missing_scope,
                "fragments": [_canonical_fragment(fragment)
                              for fragment in context.get("fragments", [])
                              if fragment["span"]["version_id"] in allowed],
                "registered_gaps": model_registered,
                "scope_acquisition_state": [{
                    "gap_id": gap_id,
                    "version_id": gap["locator"],
                    "status": (
                        "returned"
                        if (gap_id, gap["locator"]) in returned_pairs
                        else "eligible"
                        if gap["locator"] in allowed
                        else "missing"),
                } for gap_id, gap in sorted(program_scope.items())],
                "retrieval_feedback": [item for item in
                                       context.get("retrieval_feedback", [])
                                       if item.get("gap_id") in registered],
                "retrieval_receipts": receipts,
                "repair": None,
            }
            if dimension == "world":
                payload.update(
                    relations=[_canonical_relation(item)
                               for item in context.get("relations", [])],
                    origins=[_canonical_origin(item)
                             for item in context.get("origins", [])])
            return payload, allowed, registered, missing_scope

        prepared = {dimension: layer_payload(dimension)
                    for dimension in ("evidence", "world")}
        digests = {dimension: hashlib.sha256(json.dumps(
            values[0], sort_keys=True, ensure_ascii=False,
            separators=(",", ":")).encode()).hexdigest()
            for dimension, values in prepared.items()}
        changed = [dimension for dimension in ("evidence", "world")
                   if digests[dimension] not in self.layer_cache[dimension]]
        self._reserve(len(changed) * (2 + 2 * self.max_repairs),
                      len(changed) * (9500 + 9500 * self.max_repairs))

        layers = {}
        for dimension in ("evidence", "world"):
            digest = digests[dimension]
            if digest in self.layer_cache[dimension]:
                cached = self.layer_cache[dimension][digest]
                layers[dimension] = cached["assembled"]
                self.history.append({
                    "sequence": len(self.history) + 1,
                    "material": None,
                    "round": context.get("usage", {}).get("rounds", 0),
                    "stage": dimension,
                    "repair": 0,
                    "prompt_version": PROMPT_VERSION,
                    "payload_sha256": digest,
                    "status": "reused_validated_layer",
                    "transaction_id": self.current_transaction,
                    "source_transaction_id": cached["transaction_id"],
                })
                continue
            payload, allowed, registered, missing_scope = prepared[dimension]
            prompt = EVIDENCE_PROMPT if dimension == "evidence" else WORLD_PROMPT
            critic_prompt = (EVIDENCE_CRITIC_PROMPT if dimension == "evidence"
                             else WORLD_CRITIC_PROMPT)
            critic_stage = dimension + "_critic"
            repair = None
            repairs_used = 0
            while True:
                call_payload = {**payload, "repair": repair}
                raw = self._call(dimension, prompt, call_payload, LAYER_SCHEMA,
                                 None, context, repairs_used)
                try:
                    assembled = self._assemble_layer(
                        target, dimension, raw, allowed, registered,
                        missing_scope, plan,
                        payload["retrieval_receipts"],
                        payload["retrieval_feedback"])
                except ValueError as exc:
                    if repairs_used >= self.max_repairs:
                        self.history[-1]["status"] = "repair_exhausted"
                        raise StagedSemanticError(dimension, str(exc)) from None
                    self.history[-1]["status"] = "repair_requested"
                    repairs_used += 1
                    repair = _gate_repair(exc, raw)
                    continue
                self.history[-1]["status"] = "accepted"
                critic_payload = {
                    "target": asdict(target), "target_plan": plan,
                    "draft": raw,
                    "fragments": payload["fragments"],
                    "materials": payload["materials"],
                    "registered_gaps": payload["registered_gaps"],
                    "scope_acquisition_state": payload["scope_acquisition_state"],
                    "retrieval_feedback": payload["retrieval_feedback"],
                }
                if dimension == "world":
                    critic_payload.update(
                        relations=payload["relations"], origins=payload["origins"])
                critic = self._call(
                    critic_stage, critic_prompt, critic_payload,
                    LAYER_CRITIC_SCHEMA, None, context, repairs_used)
                try:
                    critic_basis = []
                    for item in critic["basis"]:
                        if item["version_id"] not in allowed:
                            raise ValueError("critic basis is outside its layer scope")
                        critic_basis.append(_span(
                            allowed[item["version_id"]], item["quote"]))
                    if len(set(critic_basis)) != len(critic_basis):
                        raise ValueError("duplicate critic basis")
                    if critic["decision"] == "accept":
                        if critic["issue"] or critic_basis:
                            raise ValueError("critic accept must have no repair instruction")
                    elif not critic["issue"].strip():
                        raise ValueError("critic repair or reject needs an issue")
                except ValueError as exc:
                    self.history[-1]["status"] = "failed"
                    raise StagedSemanticError(critic_stage, str(exc)) from None
                if critic["decision"] == "accept":
                    self.history[-1]["status"] = "accepted"
                    # A gap or resolution is a state transition, not a pure
                    # judgement. Replaying it when a payload later returns to
                    # an older shape could resurrect a closed task or repeat a
                    # stale closure. Cache only lifecycle-free assessments.
                    if not assembled["gaps"] and not assembled["resolutions"]:
                        self.layer_cache[dimension][digest] = {
                            "assembled": assembled, "wire": raw,
                            "transaction_id": self.current_transaction}
                    layers[dimension] = assembled
                    break
                if critic["decision"] == "reject" or repairs_used >= self.max_repairs:
                    self.history[-1]["status"] = (
                        "rejected" if critic["decision"] == "reject"
                        else "repair_exhausted")
                    reason = ("draft rejected" if critic["decision"] == "reject"
                              else "repair budget exhausted")
                    raise StagedSemanticError(critic_stage, reason)
                self.history[-1]["status"] = "repair_requested"
                repairs_used += 1
                repair = {"issue": critic["issue"], "basis": critic["basis"],
                          "previous_draft": raw}

        evidence, world = layers["evidence"], layers["world"]
        result = p.VerificationResult(
            verdict=evidence["verdict"], basis=evidence["basis"],
            rationale=evidence["rationale"],
            evidence_verdict=evidence["verdict"],
            world_verdict=world["verdict"], world_basis=world["basis"],
            world_rationale=world["rationale"],
            gaps=evidence["gaps"] + world["gaps"],
            resolutions=evidence["resolutions"] + world["resolutions"],
        )
        self._assembly_audit(
            "judgement_assembly", None, context,
            {"target_plan": plan, "layer_payload_sha256": digests,
             "accepted_or_reused_layers": layers},
            asdict(result))
        return result

    def _assemble_layer(self, target, dimension, raw, allowed, registered,
                        missing_scope, plan, receipts, retrieval_feedback):
        def refs(values):
            result = []
            for item in values:
                if item["version_id"] not in allowed:
                    raise ValueError("basis is outside the permitted layer materials")
                result.append(_span(allowed[item["version_id"]], item["quote"]))
            if len(set(result)) != len(result):
                raise ValueError("duplicate basis quotes")
            return tuple(result)

        def coalesce_adjacent_spans(spans):
            """Merge exact spans only across visible punctuation/whitespace.

            The returned synthesized span is persisted in the final basis, so
            deterministic grounding never reads text that the audit omits.
            """
            grouped = {}
            for span in spans:
                grouped.setdefault(span.version_id, []).append(span)
            result = []
            for version_id, selected in grouped.items():
                content = allowed[version_id]["content"]
                selected = sorted(
                    set(selected), key=lambda span: (span.start, span.end))
                current_start, current_end = selected[0].start, selected[0].end
                for span in selected[1:]:
                    gap = content[current_end:span.start]
                    whitespace_only = (
                        len(gap) <= MAX_ADJACENT_GAP_CHARS
                        and (not gap or gap.isspace()))
                    if span.start <= current_end or whitespace_only:
                        current_end = max(current_end, span.end)
                    else:
                        result.append(p.Span(
                            version_id, current_start, current_end,
                            content[current_start:current_end]))
                        current_start, current_end = span.start, span.end
                result.append(p.Span(
                    version_id, current_start, current_end,
                    content[current_start:current_end]))
            return tuple(result)

        def contains_span(container, child):
            return (container.version_id == child.version_id
                    and container.start <= child.start
                    and container.end >= child.end)

        deferred_evidence_scope = (
            {version_id for version_id in target.evidence_scope
             if version_id not in allowed}
            if dimension == "world" and target.assessment_mode == "evidence"
            else set())
        unavailable_program_scope = set(missing_scope) | deferred_evidence_scope

        def is_program_scope_wrapper(item):
            return (item["action"] == "fetch"
                    and item["locator"] in unavailable_program_scope)

        probes = {item["number"]: item for item in plan["probes"]}
        results = raw["probe_results"]
        if len(results) != len(probes) or {item["probe_number"] for item in results} != set(probes):
            raise ValueError("probe_coverage")

        gaps, resolutions, task_keys, retrieval_keys, resolution_ids = (
            [], [], set(), set(), set())
        verdicts, all_basis, rationales = [], [], []
        for result in sorted(results, key=lambda item: item["probe_number"]):
            probe = probes[result["probe_number"]]
            basis_pool = refs(result["basis_pool"])
            checks = result["dimension_results"]
            expected_dimensions = set(probe["required_dimensions"])
            if (len(checks) != len(expected_dimensions)
                    or {item["dimension"] for item in checks} != expected_dimensions):
                raise ValueError(f"dimension_coverage:{probe['number']}")
            prepared_checks, conclusive_basis = [], []
            for check in sorted(checks, key=lambda item: item["dimension"]):
                indices = check["basis_indices"]
                if len(indices) != len(set(indices)) or any(
                        index >= len(basis_pool) for index in indices):
                    raise ValueError(
                        f"dimension_basis_index:{probe['number']}:{check['dimension']}")
                check_basis = tuple(basis_pool[index] for index in indices)
                prepared_checks.append((check, check_basis))
                if check["verdict"] != "unresolved":
                    conclusive_basis.extend(check_basis)

            shared_passages = coalesce_adjacent_spans(conclusive_basis)
            check_verdicts, probe_basis, check_rationales = [], [], []
            for check, check_basis in prepared_checks:
                if check["verdict"] != "unresolved":
                    if not check_basis or missing_scope:
                        raise ValueError(
                            f"dimension_basis_or_scope:{probe['number']}:{check['dimension']}")
                    candidates = [
                        passage for passage in shared_passages
                        if all(contains_span(passage, span)
                               for span in check_basis)
                    ]
                    grounded = next((
                        passage for passage in candidates
                        if dimension_evidence_is_grounded(
                            check["dimension"], probe["text"], passage.quote,
                            check["verdict"])
                        and any(dimension_signal_is_grounded(
                            check["dimension"], probe["text"], local.quote,
                            check["verdict"])
                            for local in coalesce_adjacent_spans(check_basis))),
                        None)
                    if grounded is None:
                        raise ValueError(
                            f"dimension_grounding:{probe['number']}:{check['dimension']}")
                    probe_basis.append(grounded)
                else:
                    probe_basis.extend(check_basis)
                check_verdicts.append(check["verdict"])
                check_rationales.append(
                    check["dimension"] + "=" + check["verdict"] + ": " +
                    check["rationale"])
            verdict_by_dimension = {
                item["dimension"]: item["verdict"] for item in checks
            }
            actor_scope_supported = all(
                verdict_by_dimension.get(name) == "supported"
                for name in ("actor_subject", "scope_location"))
            predicate_verdict = verdict_by_dimension.get("predicate_object")
            if (not actor_scope_supported
                    or predicate_verdict not in {
                        "supported", "contradicted", "conflicting"}):
                # Qualifiers can decide an outcome only after one grounded
                # passage affirmatively identifies actor and scope, while the
                # action itself must be supported or explicitly contradicted.
                verdict = "unresolved"
            elif predicate_verdict == "contradicted":
                verdict = "contradicted"
            elif predicate_verdict == "conflicting":
                verdict = "conflicting"
            elif "contradicted" in check_verdicts:
                verdict = "contradicted"
            elif "conflicting" in check_verdicts:
                verdict = "conflicting"
            elif check_verdicts and all(item == "supported" for item in check_verdicts):
                verdict = "supported"
            else:
                verdict = "unresolved"
            basis = tuple(sorted(set(probe_basis),
                                 key=lambda span: (span.version_id, span.start,
                                                   span.end, span.quote)))
            stop = result["stop_reason"]
            if verdict != "unresolved" and stop != "none":
                raise ValueError("conclusive probe cannot carry a stop reason")
            if (verdict == "unresolved" and stop != "none"
                    and any(not is_program_scope_wrapper(item)
                            for item in result["gaps"])):
                raise ValueError(
                    f"stop_task_exclusivity:{probe['number']}")
            if (verdict == "unresolved" and stop == "scope_unavailable"
                    and not unavailable_program_scope):
                raise ValueError("scope_unavailable needs a missing scoped version")
            elif verdict == "unresolved" and stop == "no_source_lead":
                exhausted_ids = {item.get("gap_id") for item in retrieval_feedback
                                 if item.get("status") == "unavailable"}
                exhausted_for_probe = any(
                    gap_id in exhausted_ids
                    and registered[gap_id].get("probe_id") == probe["id"]
                    for gap_id in registered)
                frozen_scope_exhausted = (
                    dimension == "evidence" and bool(target.evidence_scope)
                    and not missing_scope)
                auxiliary_layer = dimension != target.assessment_mode
                if not (exhausted_for_probe or frozen_scope_exhausted
                        or auxiliary_layer):
                    raise ValueError("no_source_lead requires an exhausted task receipt")
            elif (verdict == "unresolved" and stop == "none"
                  and not result["gaps"] and not missing_scope):
                raise ValueError("unresolved probe cannot silently end the loop")

            verdicts.append(verdict)
            all_basis.extend(basis)
            rationale = "; ".join(check_rationales)
            if stop != "none":
                rationale += " [stop=" + stop + "]"
            rationales.append(f"probe {probe['number']} ({probe['text']}): {rationale}")

            for item in result["gaps"]:
                support = refs(item["basis"])
                if item["blocking"] and not support:
                    raise ValueError("blocking verification gap needs source basis")
                locator, action = item["locator"], item["action"]
                if is_program_scope_wrapper(item):
                    # Frozen-scope acquisition is an evidence-program-owned task
                    # with a canonical ID. Ignore duplicate evidence wrappers and
                    # auxiliary-world wrappers for that exact missing version so
                    # neither can steal the locator or evade automatic closure.
                    continue
                if action == "reanalyse":
                    if locator not in allowed:
                        raise ValueError("reanalysis must name an available layer version")
                    if support and locator not in {span.version_id for span in support}:
                        raise ValueError("reanalysis locator must be grounded by its source basis")
                elif action == "fetch":
                    parsed = urlsplit(locator)
                    if parsed.scheme not in ("http", "https") or not parsed.netloc:
                        raise ValueError("fetch locator must be an explicit HTTP URL")
                    if any(material["url"] == locator for material in allowed.values()):
                        raise ValueError("visible URL requires reanalysis, not another fetch")
                    if not any(locator in span.quote for span in support):
                        raise ValueError("fetch URL must occur in source basis")
                else:
                    lead = evidence_terms(locator)
                    grounding = evidence_terms(
                        probe["text"] + " " +
                        " ".join(span.quote for span in support))
                    if not lead or not lead & grounding:
                        raise ValueError("search locator lacks a grounded target/task term")
                key = (probe["id"], action, locator)
                if key in task_keys:
                    raise ValueError("duplicate verification task")
                task_keys.add(key)
                retrieval_keys.add((action, locator))
                candidates = [gap for gap in registered.values()
                              if (gap["action"], gap.get("locator")) == (action, locator)
                              and gap.get("probe_id") == probe["id"]]
                known = candidates[0] if len(candidates) == 1 else None
                gap_id = (known["id"] if known else _id(
                    target.id, "verification:" + dimension,
                    [probe["id"], action, locator]))
                gaps.append(p.Gap(
                    gap_id, item["question"], stage="verification",
                    dimension=dimension, blocking=item["blocking"],
                    target_id=target.id, basis=support,
                    decision_impact=item["decision_impact"],
                    action=action, locator=locator, probe_id=probe["id"]))

            for item in result["resolutions"]:
                gap_id = item["gap_id"]
                if gap_id in resolution_ids:
                    raise ValueError("duplicate resolution across target probes")
                resolution_ids.add(gap_id)
                if gap_id not in registered:
                    raise ValueError("resolution needs one registered layer gap")
                program_scope_gap = _is_program_scope_gap(
                    target, dimension, gap_id, registered[gap_id])
                if (registered[gap_id].get("probe_id") != probe["id"]
                        and not program_scope_gap):
                    raise ValueError("resolution belongs to a different target probe")
                support = refs(item["basis"])
                if not support:
                    raise ValueError("resolution needs fresh source basis")
                attributed_versions = {receipt["version_id"] for receipt in receipts
                                       if gap_id in receipt.get("task_ids", [])}
                if not attributed_versions:
                    raise ValueError("resolution requires an exact current-round task receipt")
                if not (attributed_versions & {span.version_id for span in support}):
                    raise ValueError("resolution basis does not use its attributed return")
                resolutions.append(p.Resolution(
                    gap_id, support, item["rationale"]))

        # Missing frozen evidence versions are executable tasks owned by the
        # program, not grounds for a guessed conclusion.
        for version_id in missing_scope:
            key = ("fetch", version_id)
            if key not in retrieval_keys:
                gaps.append(p.Gap(
                    _id(target.id, "missing-scope", version_id),
                    "Retrieve the frozen evidence-scope version: " + version_id,
                    stage="verification", dimension="evidence", blocking=False,
                    target_id=target.id,
                    decision_impact="The missing scoped version can change evidence entailment.",
                    action="fetch", locator=version_id))
                retrieval_keys.add(key)

        # Missing-scope tasks are program-owned. Once the exact scoped version
        # is eligible and returned under that task ID, close the task even if
        # the model omits a redundant resolution. This prevents a completed
        # evidence judgement from retaining a stale executable gap.
        if dimension == "evidence":
            receipt_versions = {
                gap_id: {receipt["version_id"] for receipt in receipts
                         if gap_id in receipt.get("task_ids", [])}
                for gap_id in registered
            }
            existing_resolution_ids = {item.gap_id for item in resolutions}
            program_scope_gaps = {
                gap_id: gap for gap_id, gap in registered.items()
                if _is_program_scope_gap(target, dimension, gap_id, gap)
            }
            for gap_id, gap in program_scope_gaps.items():
                version_id = gap.get("locator")
                if (gap_id not in existing_resolution_ids
                        and version_id in allowed
                        and version_id in receipt_versions.get(gap_id, set())):
                    material = allowed[version_id]
                    basis = (p.Span(version_id, 0, len(material["content"]),
                                    material["content"]),)
                    resolutions.append(p.Resolution(
                        gap_id, basis,
                        "The exact frozen evidence-scope version was returned and admitted."))
                    existing_resolution_ids.add(gap_id)

        active_gap_ids = {gap.id for gap in gaps}
        if active_gap_ids & {item.gap_id for item in resolutions}:
            raise ValueError("one layer cannot reopen and resolve the same gap")

        if "contradicted" in verdicts:
            verdict = "contradicted"
        elif "conflicting" in verdicts:
            verdict = "conflicting"
        elif verdicts and all(item == "supported" for item in verdicts):
            verdict = "supported"
        else:
            verdict = "unresolved"
        merged_basis = tuple(sorted(set(all_basis),
                                    key=lambda span: (span.version_id, span.start,
                                                      span.end, span.quote)))

        return {
            "verdict": verdict, "basis": merged_basis,
            "rationale": "\n".join(rationales),
            "gaps": tuple(sorted(gaps, key=lambda item: item.id)),
            "resolutions": tuple(sorted(resolutions, key=lambda item: item.gap_id)),
        }


__all__ = ["StagedDecomposer", "StagedVerifier", "StagedSemanticError"]
