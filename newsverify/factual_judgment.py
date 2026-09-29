"""Evidence-grounded world checks and bounded, reproducible arithmetic.

The model interprets records; Python validates citations, recomputes declared
arithmetic and gates unsupported certainty. This is not a measurement oracle.
"""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re

from .model_runner import COMMON, _array, _object
from .provenance import Gap, Resolution, Span, VerificationResult


CHECK_KINDS = ("target_scope", "primary_record", "method", "calculation",
               "source_dependence", "counterevidence")
STRING = {"type": "string"}
QUOTE = _object(version_id=STRING, quote=STRING)


def enum(*values):
    return {"type": "string", "enum": list(values)}


SCHEMA = _object(
    evidence_verdict=enum("supported", "contradicted", "conflicting", "unresolved"),
    evidence_basis=_array(QUOTE),
    world_verdict=enum("supported", "contradicted", "conflicting", "unresolved"),
    world_basis=_array(QUOTE), rationale=STRING,
    checks=_array(_object(kind=enum(*CHECK_KINDS),
        requirement=enum("required", "not_applicable"),
        status=enum("verified", "failed", "missing", "not_applicable"),
        basis=_array(QUOTE), rationale=STRING, question=STRING,
        action=enum("fetch", "search", "reanalyse"),
        locator={"type": ["string", "null"]})),
    calculations=_array(_object(id=STRING,
        operation=enum("sum", "difference", "mean", "ratio", "percentage", "percent_change"),
        operands=_array(_object(value=STRING, unit=STRING, basis=QUOTE)),
        claimed_value=STRING, target_quote=STRING, rounding_places=STRING)),
)

PROMPT = COMMON + """
Judge the current real-world target. Do not predict a future outcome or estimate
withdrawal/fraud risk. Return separate textual-evidence and world verdicts.
Complete exactly one check for each of: target_scope, primary_record, method,
calculation, source_dependence, counterevidence. Explain every applicability
choice. Target scope and primary record are always required.

Scope binds actor, event/measurement, date, conditions, quantity, denominator,
negation and attribution to this exact target. A primary record must be suitable
for this target: an original document settles what it reports, but its author's
assertion alone does not authenticate an experiment or underlying measurement.
For an empirical claim examine available raw records/data, methods and relevant
independent evidence. A method limitation or missing dataset is not itself proof
of falsity. A decisive falsifying record can justify contradicted. Missing or
inapplicable evidence must not be turned into a definite world verdict.

Verified checks and definite verdicts need exact nonempty source quotations.
Check source dependence before treating repeated claims as corroboration.
Check available counterevidence, corrections and limitations; absence of a
critique in this finite packet is not proof of authenticity. Any required failed
or missing check needs a precise follow-up question and fetch/search/reanalyse
action. Use a URL/version locator when available. A required check is verified
only when its question is settled by the quoted evidence.

For a derived numeric claim declare its reproducible calculations when operands
are available. Supported operations: sum/mean over 1–32 values; difference,
ratio, percentage (=100*numerator/denominator), and percent_change
(=100*(after-before)/before) over exactly two ordered values. Values are plain
signed decimals, with no scientific notation or thousands separators. Cite
each operand in its exact source passage, use one compatible unit for operands,
and quote the target passage containing the claimed number. Declare 0–6 decimal
places for rounding; Python recomputes the result. If the claim is a direct
measurement with no derivation, explain why calculation is not applicable.
Mark calculation verified only with at least one such declaration. If a needed
derivation is unavailable or cannot be expressed, keep that check missing and
request the required data or independent recalculation.
Never invent a missing operand, new observation or unavailable source.
Return the requested JSON object, with concise reasons.
"""

NUMBER = re.compile(r"(?<![\w.,+\-\u2212])[-+]?\d+(?:\.\d+)?(?!\w|\.\d|,\d)")
DECIMAL = re.compile(r"[-+]?\d+(?:\.\d+)?\Z")


def _span(item, materials):
    material = materials.get(item["version_id"])
    quote = item["quote"]
    if material is None or not quote:
        raise ValueError("Fact evidence requires an eligible version and nonempty quotation")
    text = material["content"]
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        raise ValueError("Fact quotation must occur exactly once in the original version")
    return Span(item["version_id"], start, start + len(quote), quote)


def _basis(items, materials):
    return tuple(_span(item, materials) for item in items)


def _decimal(value):
    if len(value) > 64 or not DECIMAL.fullmatch(value):
        raise ValueError("Calculation values must be bounded plain decimal strings")
    return Decimal(value)


def _number_in(value, quote):
    return any(_decimal(token.group()) == value for token in NUMBER.finditer(quote))


def recompute(item, target, materials):
    """Recompute a source-bound arithmetic assertion; never execute model code."""
    values, spans, units = [], [], []
    for operand in item["operands"]:
        span = _span(operand["basis"], materials)
        value = _decimal(operand["value"])
        if not _number_in(value, span.quote):
            raise ValueError("Calculation operand is absent from its exact evidence passage")
        if not operand["unit"].strip():
            raise ValueError("Calculation operand needs an explicit unit")
        values.append(value)
        spans.append(span)
        units.append(operand["unit"].strip().casefold())
    if not values or len(values) > 32 or len(set(units)) != 1:
        raise ValueError("Calculation needs 1–32 operands with compatible units")
    operation = item["operation"]
    if operation not in {"sum", "mean"} and len(values) != 2:
        raise ValueError("Binary calculation requires exactly two ordered operands")
    places = item["rounding_places"]
    if places not in {str(n) for n in range(7)}:
        raise ValueError("Calculation rounding must be 0–6 decimal places")
    claimed = _decimal(item["claimed_value"])
    target_quote = item["target_quote"]
    start = target.text.find(target_quote) if target_quote else -1
    if (start < 0 or target.text.find(target_quote, start + 1) >= 0
            or not _number_in(claimed, target_quote)):
        raise ValueError("Claimed number needs a unique exact target passage")
    result = {"id": item["id"], "operation": operation, "claimed_value": str(claimed),
        "target_quote": target_quote, "basis": [asdict(span) for span in spans],
        "rounding_places": int(places), "computed_value": None, "status": "undefined"}
    if operation in {"ratio", "percentage", "percent_change"}:
        denominator = values[0] if operation == "percent_change" else values[1]
        if denominator == 0:
            return result
    with localcontext() as precision:
        precision.prec = 160
        if operation == "sum":
            computed = sum(values, Decimal(0))
        elif operation == "mean":
            computed = sum(values, Decimal(0)) / len(values)
        elif operation == "difference":
            computed = values[0] - values[1]
        elif operation == "ratio":
            computed = values[0] / values[1]
        elif operation == "percentage":
            computed = 100 * values[0] / values[1]
        else:
            computed = 100 * (values[1] - values[0]) / values[0]
        rounded = computed.quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)
    result.update(computed_value=format(rounded, "f"),
                  status="match" if rounded == claimed else "mismatch")
    return result


class FactualVerifier:
    """Same Verifier interface, with explicit factual obligations and receipts."""
    def __init__(self, transport):
        self.transport = transport
        self.history = []

    def verify(self, target, context):
        from .double_loop import validate_full_output
        materials = {item["version_id"]: item for item in context["materials"]}
        registered = {}
        for prior in [*context.get("verification_history", []), {"gaps": context.get("gaps", [])}]:
            for gap in prior.get("gaps", []):
                if gap["id"].startswith("fact:" + target.id + ":"):
                    registered[gap["id"]] = gap
        packet = {"target": asdict(target), "materials": list(materials.values()),
            "lineage": [{key: edge[key] for key in ("from_version", "to_version", "kind", "status", "basis")}
                for edge in context.get("relations", []) if edge["kind"] in
                {"quotes", "cites", "reprints", "translates", "derives"}],
            "open_fact_tasks": [item for item in context.get("gaps", []) if item["id"] in registered]}
        response = self.transport.generate("fact_verify", PROMPT, packet, SCHEMA)
        validate_full_output(response, SCHEMA)
        checks = response["checks"]
        if len(checks) != len(CHECK_KINDS) or {c["kind"] for c in checks} != set(CHECK_KINDS):
            raise ValueError("Fact judgment requires exactly one check of each category")
        if not response["rationale"].strip():
            raise ValueError("Fact judgment needs a substantive rationale")
        evidence_basis = _basis(response["evidence_basis"], materials)
        world_basis = _basis(response["world_basis"], materials)
        for kind, basis in (("evidence", evidence_basis), ("world", world_basis)):
            if response[kind + "_verdict"] in {"supported", "contradicted"} and not basis:
                raise ValueError("A definite " + kind + " verdict needs exact evidence")
        gaps, resolutions, audit_checks, blocked = [], [], [], []

        def task(identifier, question, action, locator, basis, impact):
            old = registered.get(identifier)
            if old is not None:
                if old["stage"] != "verification" or old["dimension"] != "world":
                    raise ValueError("Registered factual gap has incompatible lifecycle")
                old_basis = tuple(Span(**span) for span in old.get("basis", []))
                return Gap(identifier, question, "verification", "world", old["blocking"],
                    old["target_id"], basis or old_basis, impact, old["action"], old["locator"], old.get("probe_id"))
            return Gap(identifier, question, "verification", "world", bool(basis),
                target.id, basis, impact, action, locator)

        for check in checks:
            kind = check["kind"]
            identifier = "fact:" + target.id + ":" + kind
            basis = _basis(check["basis"], materials)
            if not check["rationale"].strip():
                raise ValueError("Each fact check must explain its finding or applicability")
            if kind in {"target_scope", "primary_record"} and check["requirement"] != "required":
                raise ValueError("Target scope and primary record cannot be waived")
            if (check["requirement"] == "not_applicable") != (check["status"] == "not_applicable"):
                raise ValueError("Check applicability and finding disagree")
            if check["status"] == "verified" and not basis:
                raise ValueError("A verified fact check requires exact evidence")
            if kind == "calculation" and check["status"] == "verified" and not response["calculations"]:
                raise ValueError("A verified calculation requires source-bound recomputation")
            audit_checks.append({**deepcopy(check), "basis": [asdict(span) for span in basis]})
            if check["requirement"] == "required" and check["status"] != "verified":
                if not check["question"].strip():
                    raise ValueError("An unsettled required fact check needs an executable question")
                blocked.append(identifier)
                gaps.append(task(identifier, check["question"], check["action"], check["locator"], basis,
                    "Required " + kind + " check is not established: " + check["rationale"]))
            elif check["status"] == "verified" and identifier in registered:
                resolutions.append(Resolution(identifier, basis, check["rationale"]))
        calc_ids = [item["id"] for item in response["calculations"]]
        if any(not identifier.strip() for identifier in calc_ids) or len(calc_ids) != len(set(calc_ids)):
            raise ValueError("Calculation IDs must be nonempty and unique")
        calculations = [recompute(item, target, materials) for item in response["calculations"]]
        for item in calculations:
            identifier = "fact:" + target.id + ":calculation:" + item["id"]
            basis = tuple(Span(**span) for span in item["basis"])
            if item["status"] != "match":
                if response["world_verdict"] != "contradicted" or item["status"] == "undefined":
                    blocked.append(identifier)
                    gaps.append(task(identifier,
                        "Reconcile the target's " + item["claimed_value"] + " with the source-bound "
                        + item["operation"] + " result " + str(item["computed_value"]) + ".",
                        "reanalyse", basis[0].version_id, basis,
                        "The declared arithmetic is " + item["status"] + "; a positive fact verdict is unsupported."))
                elif identifier in registered:
                    resolutions.append(Resolution(identifier, basis,
                        "Source-bound arithmetic settles the discrepancy as a contradiction: "
                        + response["rationale"]))
            elif identifier in registered:
                resolutions.append(Resolution(identifier, basis, "Source-bound arithmetic now matches the target."))
        world = response["world_verdict"]
        if blocked and world in {"supported", "contradicted"}:
            world = "unresolved"
        self.history.append({"requested_world_verdict": response["world_verdict"],
            "world_verdict": world, "evidence_verdict": response["evidence_verdict"],
            "checks": audit_checks, "calculations": calculations, "blocked_check_ids": blocked,
            "rationale": response["rationale"], "material_version_ids": list(materials)})
        return VerificationResult(response["evidence_verdict"], evidence_basis,
            response["rationale"], tuple(gaps), tuple(resolutions),
            response["evidence_verdict"], world, world_basis, response["rationale"])
