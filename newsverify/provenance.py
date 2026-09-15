"""Versioned provenance orchestration with explicit source reanalysis policy.

This module does not infer source lineage or news truth. Plug-ins supply semantic
analyses; the runner enforces version, citation, temporal and loop contracts. The
default decomposer deliberately leaves provenance unresolved. All inputs are data.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable, Protocol


@dataclass(frozen=True)
class Target:
    id: str
    text: str
    as_of: str
    source_version_id: str | None = None
    assessment_mode: str = "world"
    evidence_scope: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterialVersion:
    version_id: str
    url: str
    content: str
    retrieved_at: str
    published_at: str | None = None
    available_at: str | None = None
    availability_basis: str | None = None
    issuer: str = "unknown"


@dataclass(frozen=True)
class RetrievalHit:
    """One immutable return plus the frozen tasks that selected it.

    Providers may still yield bare ``MaterialVersion`` objects as a legacy,
    unattributed return.  Task-aware providers should yield this envelope so
    lazy iteration cannot race a mutable side channel.
    """
    material: MaterialVersion
    task_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Span:
    version_id: str
    start: int
    end: int
    quote: str


@dataclass(frozen=True)
class Fragment:
    id: str
    text: str
    span: Span
    parent_id: str
    qualifiers: tuple[str, ...] = ()
    qualifier_spans: tuple[Span, ...] = ()


@dataclass(frozen=True)
class Relation:
    id: str
    from_version: str
    to_version: str | None
    kind: str
    status: str
    basis: tuple[Span, ...]
    rationale: str
    upstream_locator: str | None = None


@dataclass(frozen=True)
class Gap:
    id: str
    question: str
    stage: str = "provenance"
    dimension: str = "auto"
    blocking: bool = True
    target_id: str | None = None
    basis: tuple[Span, ...] = ()
    decision_impact: str = ""
    action: str = "search"
    locator: str | None = None
    probe_id: str | None = None


@dataclass(frozen=True)
class Resolution:
    gap_id: str
    basis: tuple[Span, ...]
    rationale: str


@dataclass(frozen=True)
class OriginFinding:
    target_id: str
    version_id: str
    basis: tuple[Span, ...]
    material_kind: str
    rationale: str


@dataclass(frozen=True)
class Analysis:
    fragments: tuple[Fragment, ...] = ()
    relations: tuple[Relation, ...] = ()
    gaps: tuple[Gap, ...] = ()
    resolutions: tuple[Resolution, ...] = ()
    origins: tuple[OriginFinding, ...] = ()
    notes: str = ""
    revisit_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    verdict: str = "unresolved"
    basis: tuple[Span, ...] = ()
    rationale: str = ""
    gaps: tuple[Gap, ...] = ()
    resolutions: tuple[Resolution, ...] = ()
    evidence_verdict: str | None = None
    world_verdict: str | None = None
    world_basis: tuple[Span, ...] = ()
    world_rationale: str = ""


@dataclass(frozen=True)
class TraceConfig:
    max_rounds: int = 5
    max_documents: int = 30
    max_decomposition_calls: int = 30
    reanalyze_existing_versions: bool = True


class TraceProvider(Protocol):
    def search(self, target: Target, tasks: tuple[Gap, ...], round_number: int,
               limit: int) -> Iterable[MaterialVersion | RetrievalHit]: ...


class Decomposer(Protocol):
    def decompose(self, target: Target, material: MaterialVersion,
                  context: dict) -> Analysis: ...


class Verifier(Protocol):
    def verify(self, target: Target, context: dict) -> VerificationResult: ...


class ConservativeDecomposer:
    """Keep the whole text verbatim; no semantic or original-source inference."""

    def decompose(self, target, material, context):
        return Analysis(fragments=(Fragment(
            id=f"{material.version_id}:full", text=material.content,
            span=Span(material.version_id, 0, len(material.content), material.content),
            parent_id=target.id,
        ),), notes="Conservative full-text preservation; semantic decomposition unavailable.")


class ReplayTraceProvider:
    """Offline materials only. Rounds are indexed by provider call number."""

    def __init__(self, rounds: Iterable[Iterable[MaterialVersion]]):
        self.rounds = tuple(tuple(items) for items in rounds)

    def search(self, target, tasks, round_number, limit):
        if round_number <= len(self.rounds):
            yield from self.rounds[round_number - 1][:limit]


def _time(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing timezone")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a timezone-aware ISO timestamp") from exc


def _nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _tuple_of(items, cls, name):
    if not isinstance(items, tuple) or any(not isinstance(item, cls) for item in items):
        raise ValueError(f"{name} must be a tuple of {cls.__name__}")


def _fingerprint(material):
    # Retrieval is an observation, not part of the immutable publisher version.
    payload = asdict(material)
    payload.pop("retrieved_at")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _material_eligibility(material, cutoff):
    if not isinstance(material, MaterialVersion):
        raise ValueError("provider must yield MaterialVersion objects")
    for name in ("version_id", "url", "content", "issuer"):
        _nonempty(getattr(material, name), name)
    retrieved = _time(material.retrieved_at, "retrieved_at")
    published = _time(material.published_at, "published_at") if material.published_at is not None else None
    available = _time(material.available_at, "available_at") if material.available_at is not None else None
    if material.availability_basis is not None:
        _nonempty(material.availability_basis, "availability_basis")
    reasons = []
    if published is not None and published > retrieved:
        reasons.append("published_after_retrieval")
    if published is not None and published > cutoff:
        reasons.append("published_after_as_of")
    if available is None:
        reasons.append("version_availability_unknown")
    elif not material.availability_basis:
        reasons.append("version_availability_unsubstantiated")
    elif available > cutoff:
        reasons.append("version_available_after_as_of")
    if available is not None and available > retrieved:
        reasons.append("available_after_retrieval")
    return reasons


def _span(span, materials):
    if not isinstance(span, Span) or span.version_id not in materials:
        raise ValueError("span must reference a stored eligible material version")
    if type(span.start) is not int or type(span.end) is not int:
        raise ValueError("span offsets must be integers")
    content = materials[span.version_id].content
    if not (0 <= span.start < span.end <= len(content)) or content[span.start:span.end] != span.quote:
        raise ValueError("span quote must exactly match the original character offsets")


def _basis(items, materials):
    _tuple_of(items, Span, "basis")
    if not items:
        raise ValueError("an evidence basis is required")
    for item in items:
        _span(item, materials)


def _gap(gap, target=None, materials=None):
    if not isinstance(gap, Gap):
        raise ValueError("invalid gap type")
    _nonempty(gap.id, "gap.id")
    _nonempty(gap.question, "gap.question")
    if gap.stage not in {"provenance", "verification"}:
        raise ValueError("gap.stage must be provenance or verification")
    if gap.dimension not in {"auto", "provenance", "evidence", "world"}:
        raise ValueError("invalid gap dimension")
    if gap.dimension != "auto" and (gap.dimension == "provenance") != (gap.stage == "provenance"):
        raise ValueError("gap stage and dimension disagree")
    if type(gap.blocking) is not bool:
        raise ValueError("gap.blocking must be boolean")
    if target and gap.target_id is not None and gap.target_id != target.id:
        raise ValueError("gap belongs to another target")
    if gap.probe_id is not None:
        _nonempty(gap.probe_id, "gap.probe_id")
    if gap.action not in {"fetch", "search", "reanalyse"}:
        raise ValueError("invalid gap action")
    if gap.action in {"fetch", "reanalyse"}:
        _nonempty(gap.locator, "gap.locator")
    _tuple_of(gap.basis, Span, "gap.basis")
    if materials is not None:
        for span in gap.basis:
            _span(span, materials)
    if gap.dimension in {"evidence", "world"} and gap.blocking:
        _nonempty(gap.decision_impact, "blocking gap decision_impact")
        if not gap.basis:
            raise ValueError("an explicit blocking gap needs a source basis")


def gap_dimension(gap):
    return ("provenance" if gap.stage == "provenance" else "world") if gap.dimension == "auto" else gap.dimension


def select_assessments(check, target, gaps):
    """One shared decision policy; preserve evidence findings even when world facts are unknown."""
    evidence = check.evidence_verdict if check.evidence_verdict is not None else check.verdict
    world = check.world_verdict if check.world_verdict is not None else check.verdict
    result = {}
    for dimension, raw in (("evidence", evidence), ("world", world)):
        blockers = sorted(g.id for g in gaps if g.blocking and gap_dimension(g) == dimension)
        result[dimension] = {"raw_verdict": raw, "decision": "unresolved" if blockers else raw,
                             "blocking_gap_ids": blockers}
    return result


def _resolution(resolution, materials):
    _nonempty(resolution.gap_id, "resolution.gap_id")
    _nonempty(resolution.rationale, "resolution.rationale")
    _basis(resolution.basis, materials)


def _require_unique(items, key, name):
    """Reject ambiguous batches instead of letting a later item win in a dict."""
    seen = set()
    for item in items:
        value = key(item)
        if value in seen:
            raise ValueError(f"duplicate {name}: {value}")
        seen.add(value)


def _gap_identity(gap, target):
    """Fields that define one gap's immutable retrieval lifecycle."""
    return (gap.stage, gap_dimension(gap), gap.target_id or target.id,
            gap.action, gap.locator, gap.probe_id)


def _task_signature(gap, target):
    """Identity of one frozen executable provider obligation."""
    effective_query = gap.question if gap.locator is None else None
    return (gap.id, *_gap_identity(gap, target), effective_query)


def _register_gaps(registry, owners, items, owner, target):
    """Validate gap identity/ownership without mutating accepted state.

    Blocking severity is monotonic for the lifetime of an ID, including after
    a resolution.  A genuinely less severe follow-up is a different task and
    must receive a new ID; otherwise an old blocking task could be reopened
    later under weaker decision semantics.
    """
    _require_unique(items, lambda item: item.id, "gap id")
    updated = dict(registry)
    updated_owners = {key: set(value) for key, value in owners.items()}
    for item in items:
        previous = updated.get(item.id)
        if previous is not None:
            if _gap_identity(previous, target) != _gap_identity(item, target):
                raise ValueError(f"conflicting gap lifecycle identity: {item.id}")
            if previous.blocking and not item.blocking:
                raise ValueError(
                    f"registered blocking gap cannot be weakened under the same id: {item.id}")
            if previous != item and owner not in updated_owners[item.id]:
                raise ValueError(f"conflicting gap id across owners: {item.id}")
        updated[item.id] = item
        updated_owners.setdefault(item.id, set()).add(owner)
    return updated, updated_owners


def _validate_resolution_references(resolutions, registry, stage):
    _require_unique(resolutions, lambda item: item.gap_id, "resolution gap id")
    for item in resolutions:
        known = registry.get(item.gap_id)
        if known is None:
            raise ValueError(f"resolution references unknown gap: {item.gap_id}")
        if stage == "verification" and (known.stage != "verification" or
                                         gap_dimension(known) not in {"evidence", "world"}):
            raise ValueError(f"verifier may not resolve provenance gap: {item.gap_id}")


def _validate_analysis_collisions(analysis, current_analyses, owner):
    """A new owner may not silently redefine another analysis's finding ID."""
    fields = (("fragments", lambda item: item.id, "fragment"),
              ("relations", lambda item: item.id, "relation"),
              ("origins", lambda item: (item.target_id, item.version_id), "origin"))
    for field, key, name in fields:
        existing = {}
        for version_id, previous in current_analyses.items():
            if version_id == owner:
                continue
            for item in getattr(previous, field):
                existing[key(item)] = item
        for item in getattr(analysis, field):
            identity = key(item)
            if identity in existing and existing[identity] != item:
                raise ValueError(f"conflicting {name} id across materials: {identity}")


def _validate_active_gap_definitions(groups):
    """All current owners of one ID must project the exact same task.

    ``gap_registry`` is intentionally historical, so its owner set cannot be
    used to decide whether a current replacement is safe.  Validate the live
    owner projections before any dict rebuild can pick a last writer.
    """
    definitions = {}
    for owner, items in groups:
        for item in items:
            previous = definitions.get(item.id)
            if previous is not None and previous[1] != item:
                raise ValueError(
                    f"conflicting active gap definition across {previous[0]} and {owner}: {item.id}")
            definitions[item.id] = (owner, item)


def _combine_resolutions(items):
    """Combine independent closures of the same gap without last-writer loss."""
    grouped = {}
    for item in items:
        grouped.setdefault(item.gap_id, []).append(item)
    result = {}
    for gap_id, values in grouped.items():
        basis = sorted({span for item in values for span in item.basis},
                       key=lambda span: (span.version_id, span.start, span.end, span.quote))
        rationales = sorted({item.rationale.strip() for item in values})
        result[gap_id] = Resolution(gap_id, tuple(basis), " | ".join(rationales))
    return result


def _validate_analysis(analysis, target, material, materials):
    if not isinstance(analysis, Analysis):
        raise ValueError("decomposer must return Analysis")
    for name, cls in (("fragments", Fragment), ("relations", Relation), ("gaps", Gap),
                      ("resolutions", Resolution), ("origins", OriginFinding)):
        _tuple_of(getattr(analysis, name), cls, name)
    if not isinstance(analysis.notes, str):
        raise ValueError("analysis.notes must be a string")
    for name, key in (("fragment id", lambda item: item.id),
                      ("relation id", lambda item: item.id),
                      ("gap id", lambda item: item.id),
                      ("resolution gap id", lambda item: item.gap_id),
                      ("origin target/version", lambda item: (item.target_id, item.version_id))):
        collection = (analysis.fragments if name == "fragment id" else
                      analysis.relations if name == "relation id" else
                      analysis.gaps if name == "gap id" else
                      analysis.resolutions if name == "resolution gap id" else
                      analysis.origins)
        _require_unique(collection, key, name)
    if {item.id for item in analysis.gaps} & {
            item.gap_id for item in analysis.resolutions}:
        raise ValueError("one analysis cannot reopen and resolve the same gap")
    _tuple_of(analysis.revisit_versions, str, "revisit_versions")
    if any(version not in materials for version in analysis.revisit_versions):
        raise ValueError("revisit_versions must refer to available material versions")
    for fragment in analysis.fragments:
        for name in ("id", "text", "parent_id"):
            _nonempty(getattr(fragment, name), f"fragment.{name}")
        _span(fragment.span, materials)
        _tuple_of(fragment.qualifiers, str, "qualifiers")
        _tuple_of(fragment.qualifier_spans, Span, "qualifier_spans")
        for span in fragment.qualifier_spans:
            _span(span, materials)
    parents = {item.id: item.parent_id for item in analysis.fragments}
    for fragment in analysis.fragments:
        cursor, seen = fragment.id, set()
        while cursor != target.id:
            if cursor in seen or cursor not in parents:
                raise ValueError("fragment parents must form an acyclic path to target")
            seen.add(cursor)
            cursor = parents[cursor]
    for edge in analysis.relations:
        _nonempty(edge.id, "relation.id")
        _nonempty(edge.rationale, "relation.rationale")
        if edge.from_version not in materials:
            raise ValueError("relation source version is unavailable")
        if edge.to_version is not None and edge.to_version not in materials:
            raise ValueError("unavailable upstream needs upstream_locator and to_version=None")
        if edge.to_version is None:
            _nonempty(edge.upstream_locator, "upstream_locator")
        if edge.kind not in {"quotes", "cites", "reprints", "translates", "derives", "supports", "contradicts"}:
            raise ValueError("invalid relation kind")
        if edge.status not in {"direct", "declared", "inferred", "unresolved", "excluded"}:
            raise ValueError("invalid relation status")
        _basis(edge.basis, materials)
        if not any(item.version_id == edge.from_version for item in edge.basis):
            raise ValueError("relation basis must include a source-side span")
        if edge.status == "direct" and edge.to_version is None:
            raise ValueError("direct relation requires an available upstream version")
    for gap in analysis.gaps:
        _gap(gap, target, materials)
    for resolution in analysis.resolutions:
        _resolution(resolution, materials)
    for origin in analysis.origins:
        if origin.target_id != target.id or origin.version_id not in materials:
            raise ValueError("origin must identify the target and an eligible version")
        if origin.material_kind not in {"original_record", "original_interview", "original_dataset", "original_observation"}:
            raise ValueError("origin requires an explicit original-material kind")
        _nonempty(origin.rationale, "origin.rationale")
        _basis(origin.basis, materials)
        if not any(item.version_id == origin.version_id for item in origin.basis):
            raise ValueError("origin basis must locate evidence in the claimed original")


def run_provenance(target: Target | dict, provider: TraceProvider,
                   decomposer: Decomposer | None = None, verifier: Verifier | None = None,
                   config: TraceConfig | dict | None = None) -> dict:
    """Run a bounded trace. Provider/analysis errors are audited and fail unresolved.

    Historical eligibility needs an explicit, evidenced ``available_at`` for the
    exact version. ``published_at`` alone never proves historical availability.
    A late retrieval of an evidenced old version is allowed; there is no age cap.
    Reanalysis remains the generic default. Incremental callers can preserve an
    unchanged version's accepted analysis instead of decomposing it again.
    """
    if isinstance(target, dict):
        raw_target = dict(target)
        if isinstance(raw_target.get("evidence_scope"), list):
            raw_target["evidence_scope"] = tuple(raw_target["evidence_scope"])
        target = Target(**raw_target)
    config = TraceConfig(**config) if isinstance(config, dict) else (config or TraceConfig())
    if not isinstance(target, Target) or not isinstance(config, TraceConfig):
        raise ValueError("invalid target or config type")
    _nonempty(target.id, "target.id")
    _nonempty(target.text, "target.text")
    if target.assessment_mode not in {"evidence", "world"}:
        raise ValueError("assessment_mode must be evidence or world")
    _tuple_of(target.evidence_scope, str, "evidence_scope")
    if len(set(target.evidence_scope)) != len(target.evidence_scope):
        raise ValueError("duplicate evidence_scope version")
    if target.source_version_id is not None:
        _nonempty(target.source_version_id, "target.source_version_id")
    cutoff = _time(target.as_of, "target.as_of")
    for name in ("max_rounds", "max_documents", "max_decomposition_calls"):
        if type(getattr(config, name)) is not int or getattr(config, name) < 1:
            raise ValueError(f"{name} must be a positive integer")
    if type(config.reanalyze_existing_versions) is not bool:
        raise ValueError("reanalyze_existing_versions must be a boolean")
    decomposer = decomposer or ConservativeDecomposer()
    materials = {}
    eligible = {}
    fingerprints = {}
    current_analyses = {}
    analysis_epochs = {}
    analysis_resolution_epochs = {}
    history = []
    verifications = []
    verified_version_ids = set()
    operations = []
    observations = []
    current_round_returns = []
    retrieval_feedback_history = []
    errors = []
    initial = Gap("origin:" + target.id, "Find the producing record and evidenced lineage for: " + target.text)
    gap_registry = {initial.id: initial}
    gap_owners = {initial.id: {"runner"}}
    gaps = {initial.id: initial}
    resolved = {}
    verification_gaps = {}
    verification_gap_epochs = {}
    verification_resolved = {}
    runtime_gaps = {}
    fragments = {}
    relations = {}
    origins = {}
    usage = {"rounds": 0, "documents": 0, "unique_versions": 0, "decomposition_calls": 0, "verification_calls": 0}
    fact_status = "not_checked" if verifier is None else "unresolved"
    decision_status = fact_status
    assessments = None
    stop_reason = "round_budget"
    fatal = False

    def event(action, **values):
        operations.append({"sequence": len(operations) + 1, "round": usage["rounds"], "action": action, **values})

    def canonical_retrieval_feedback():
        latest = {}
        for item in retrieval_feedback_history:
            latest[item["gap_id"]] = {key: item[key] for key in (
                "gap_id", "action", "locator", "status", "version_ids")}
        return [latest[key] for key in sorted(latest)]

    def context():
        return deepcopy({
            "target": asdict(target), "materials": [asdict(item) for item in eligible.values()],
            "analyses": {key: asdict(item) for key, item in current_analyses.items()},
            "fragments": [asdict(item) for item in fragments.values()],
            "relations": [asdict(item) for item in relations.values()],
            "origins": [asdict(item) for item in origins.values()],
            "gaps": [asdict(item) for item in gaps.values()],
            "gap_registry": [asdict(item) for item in gap_registry.values()],
            "verification_history": deepcopy(verifications), "usage": dict(usage),
            "verified_version_ids": sorted(verified_version_ids),
            "current_round_returns": deepcopy(current_round_returns),
            "retrieval_feedback": deepcopy(canonical_retrieval_feedback()),
            "assessments": deepcopy(assessments),
        })

    def rebuild(candidate_runtime_gaps=None):
        """Build and validate a full projection, then swap it atomically."""
        proposed_runtime_gaps = (dict(runtime_gaps) if candidate_runtime_gaps is None
                                 else dict(candidate_runtime_gaps))
        candidate_registry, candidate_owners = validate_live_projection(
            current_analyses, verification_gaps, proposed_runtime_gaps,
            eligible, gap_registry, gap_owners)
        proposed_fragments = {}
        proposed_relations = {}
        proposed_origins = {}
        proposed_gaps = {initial.id: initial, **proposed_runtime_gaps}
        proposed_resolution_items = []
        analysis_gap_events = {}
        analysis_resolution_events = {}
        for version_id, analysis in current_analyses.items():
            epoch = analysis_epochs[version_id]
            for item in analysis.fragments:
                proposed_fragments[item.id] = item
            for item in analysis.relations:
                proposed_relations[item.id] = item
            for item in analysis.origins:
                proposed_origins[(item.target_id, item.version_id)] = item
            for item in analysis.gaps:
                analysis_gap_events.setdefault(item.id, []).append((epoch, item))
            for item in analysis.resolutions:
                analysis_resolution_events.setdefault(item.gap_id, []).append(
                    (analysis_resolution_epochs[version_id].get(item.gap_id, epoch), item))
        # A gap and its resolution are ordered state transitions. Current
        # analysis snapshots retain only each owner's latest revision, then
        # the latest explicit open/close event wins across owners. This lets a
        # newer material reopen an older closure without discarding audit
        # history, and lets a later resolution close an older open task.
        for gap_id in sorted(set(analysis_gap_events) | set(analysis_resolution_events)):
            gap_events = analysis_gap_events.get(gap_id, [])
            resolution_events = analysis_resolution_events.get(gap_id, [])
            latest_gap = max((epoch for epoch, _ in gap_events), default=-1)
            latest_resolution = max(
                (epoch for epoch, _ in resolution_events), default=-1)
            if latest_gap > latest_resolution:
                proposed_gaps[gap_id] = max(gap_events, key=lambda value: value[0])[1]
            else:
                # Combine independent closures that occurred after the most
                # recent live reopen; earlier closures are historical only.
                proposed_resolution_items.extend(
                    item for epoch, item in resolution_events if epoch > latest_gap)
        # Preserve verification tasks until explicitly resolved by either stage.
        proposed_gaps.update(verification_gaps)
        proposed_resolution_items.extend(verification_resolved.values())
        proposed_resolved = _combine_resolutions(proposed_resolution_items)
        # Active verifier tasks are newer than the current analysis snapshots.
        # A later psi resolution removes them transactionally in ``analyze``;
        # until then the explicit reopen wins over older analysis closures.
        for gap_id in verification_gaps:
            proposed_resolved.pop(gap_id, None)
        if proposed_origins and not has_origin_path_for(current_analyses, eligible):
            lineage_gap = Gap("lineage:" + target.id,
                "Provide the target source version and an evidenced citation/derivation path to an original material")
            proposed_gaps[lineage_gap.id] = lineage_gap
        proposed_active_gaps = {
            key: value for key, value in proposed_gaps.items()
            if key not in proposed_resolved
        }

        # No accepted dictionary is mutated before all candidate validation and
        # assembly above succeeds. This keeps the public projection consistent
        # with committed analyses even when a runner-owned task collides.
        runtime_gaps.clear()
        runtime_gaps.update(proposed_runtime_gaps)
        gap_registry.clear()
        gap_registry.update(candidate_registry)
        gap_owners.clear()
        gap_owners.update(candidate_owners)
        fragments.clear()
        fragments.update(proposed_fragments)
        relations.clear()
        relations.update(proposed_relations)
        origins.clear()
        origins.update(proposed_origins)
        gaps.clear()
        gaps.update(proposed_active_gaps)
        resolved.clear()
        resolved.update(proposed_resolved)

    def has_origin_path():
        if target.source_version_id is None or target.source_version_id not in eligible:
            return False
        roots = {item.version_id for item in origins.values()}
        adjacency = {}
        for edge in relations.values():
            if edge.status == "direct" and edge.kind in {"quotes", "cites", "reprints", "translates", "derives"}:
                adjacency.setdefault(edge.from_version, set()).add(edge.to_version)
        pending = [target.source_version_id]
        visited = set()
        while pending:
            version = pending.pop()
            if version in roots:
                return True
            if version not in visited:
                visited.add(version)
                pending.extend(adjacency.get(version, ()))
        return False

    def has_origin_path_for(analyses, admitted):
        if target.source_version_id is None or target.source_version_id not in admitted:
            return False
        candidate_origins = [item for analysis in analyses.values()
                             for item in analysis.origins]
        candidate_relations = [item for analysis in analyses.values()
                               for item in analysis.relations]
        roots = {item.version_id for item in candidate_origins}
        adjacency = {}
        for edge in candidate_relations:
            if (edge.status == "direct"
                    and edge.kind in {"quotes", "cites", "reprints", "translates", "derives"}):
                adjacency.setdefault(edge.from_version, set()).add(edge.to_version)
        pending, visited = [target.source_version_id], set()
        while pending:
            version = pending.pop()
            if version in roots:
                return True
            if version not in visited:
                visited.add(version)
                pending.extend(adjacency.get(version, ()))
        return False

    def validate_live_projection(analyses, verifier_tasks, runner_tasks,
                                 admitted, registry, owners):
        """Validate a complete candidate state before committing any part."""
        groups = [("runner:origin", (initial,)),
                  ("runner:runtime", tuple(runner_tasks.values()))]
        groups.extend(("material:" + version, analysis.gaps)
                      for version, analysis in analyses.items())
        groups.append(("verifier", tuple(verifier_tasks.values())))
        projected_registry, projected_owners = _register_gaps(
            registry, owners, tuple(runner_tasks.values()), "runner", target)
        candidate_origins = [item for analysis in analyses.values()
                             for item in analysis.origins]
        if candidate_origins and not has_origin_path_for(analyses, admitted):
            lineage_gap = Gap(
                "lineage:" + target.id,
                "Provide the target source version and an evidenced citation/derivation path to an original material")
            groups.append(("runner:lineage", (lineage_gap,)))
            projected_registry, projected_owners = _register_gaps(
                projected_registry, projected_owners, (lineage_gap,), "runner", target)
        _validate_active_gap_definitions(groups)
        return projected_registry, projected_owners

    def structural_fingerprint():
        def records(values, excluded=()):
            items = [{key: value for key, value in asdict(item).items() if key not in excluded}
                     for item in values]
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        # Natural-language paraphrases and regenerated IDs are not evidence progress.
        gap_records = []
        for gap in (g for g in gaps.values() if not g.id.startswith("revisit:")):
            # ``question`` is cosmetic when an explicit locator drives the
            # provider. Without a locator it is the effective search query and
            # therefore must be part of progress detection.
            excluded = ("id", "decision_impact", "question") if gap.locator is not None else (
                "id", "decision_impact")
            gap_records.extend(records((gap,), excluded))
        state = {"fragments": records(fragments.values(), ("id", "text", "qualifiers")),
                 "relations": records(relations.values(), ("id", "rationale")),
                 "origins": records(origins.values(), ("rationale",)),
                 "gaps": sorted(gap_records, key=lambda item: json.dumps(item, sort_keys=True))}
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    def verification_input_fingerprint():
        """Hash the exact canonical state exposed to supported verifiers."""
        # These objects are passed to the public verifier protocol in full.
        # Hash every visible field: prose, qualifiers, IDs and rationales can
        # legitimately change a custom verifier's judgement even when they are
        # deliberately excluded from structural loop-progress detection.
        canonical_analyses = {
            key: asdict(current_analyses[key]) for key in sorted(current_analyses)
        }
        canonical_fragments = [asdict(item) for item in fragments.values()]
        canonical_relations = [asdict(item) for item in relations.values()]
        canonical_origins = [asdict(item) for item in origins.values()]
        receipts = [{"version_id": item["version_id"],
                     "task_ids": item.get("task_ids", []),
                     "tasks": item.get("tasks", []),
                     "attribution": item.get("attribution")}
                    for item in current_round_returns if item.get("task_ids")]
        payload = {
            "target": asdict(target),
            "eligible_versions": {key: fingerprints[key] for key in sorted(eligible)},
            "analyses": canonical_analyses,
            "fragments": sorted(canonical_fragments,
                                key=lambda item: json.dumps(item, sort_keys=True)),
            "relations": sorted(canonical_relations,
                                key=lambda item: json.dumps(item, sort_keys=True)),
            "origins": sorted(canonical_origins,
                              key=lambda item: json.dumps(item, sort_keys=True)),
            # Gap IDs and task wording are operational verifier input: a
            # resolution must name the current exact task, not an old alias.
            "gaps": sorted((asdict(item) for item in gaps.values()),
                           key=lambda item: json.dumps(item, sort_keys=True)),
            "retrieval_receipts": sorted(
                receipts, key=lambda item: json.dumps(item, sort_keys=True)),
            "retrieval_feedback": (deepcopy(canonical_retrieval_feedback())
                                   if getattr(verifier, "uses_retrieval_feedback", False)
                                   else []),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def fail(stage, exc):
        nonlocal fatal, fact_status, decision_status, stop_reason
        fatal = True
        fact_status = "unresolved" if verifier is not None else "not_checked"
        decision_status = fact_status
        stop_reason = "integrity_error" if stage == "integrity" else f"{stage}_error"
        errors.append({"stage": stage, "type": type(exc).__name__, "message": str(exc)})
        event("error", stage=stage, message=str(exc))

    def analyze(material, reasons, duplicate, revisit=False, current_return=None):
        usage["decomposition_calls"] += 1
        event("decompose_started", version_id=material.version_id, duplicate=duplicate, revisit=revisit)
        psi_context = context()
        psi_context["current_return"] = deepcopy(current_return or {
            "version_id": material.version_id, "task_ids": [], "revisit": revisit})
        psi_context["current_material_eligible"] = not reasons
        psi_context["current_material_exclusion_reasons"] = reasons
        # Archive decomposition is isolated from stateful semantic plugins: a
        # future version must not contaminate a historical prediction.
        analysis = (ConservativeDecomposer().decompose(target, material, {}) if reasons
                    else decomposer.decompose(target, material, psi_context))
        validation_materials = dict(eligible)
        validation_materials[material.version_id] = material
        _validate_analysis(analysis, target, material, validation_materials)
        if not reasons:
            _validate_analysis_collisions(analysis, current_analyses, material.version_id)
            candidate_registry, candidate_owners = _register_gaps(
                gap_registry, gap_owners, analysis.gaps,
                "material:" + material.version_id, target)
            _validate_resolution_references(analysis.resolutions, candidate_registry, "decomposition")
            candidate_analyses = dict(current_analyses)
            candidate_analyses[material.version_id] = analysis
            candidate_eligible = dict(eligible)
            candidate_eligible[material.version_id] = material
            candidate_verification_gaps = dict(verification_gaps)
            candidate_verification_resolved = dict(verification_resolved)
            previous = current_analyses.get(material.version_id)
            previous_resolutions = {
                item.gap_id: item for item in previous.resolutions
            } if previous else {}
            previous_resolution_epochs = analysis_resolution_epochs.get(
                material.version_id, {})
            accepted_epoch = len(history) + len(verifications) + 1
            candidate_resolution_epochs = {
                item.gap_id: previous_resolution_epochs[item.gap_id]
                if item.gap_id in previous_resolutions
                and set(item.basis) == set(previous_resolutions[item.gap_id].basis)
                else accepted_epoch
                for item in analysis.resolutions
            }
            # A repeated or cosmetically reworded old resolution cannot close
            # a newer verifier request. Only evidence accepted after that
            # request supersedes it.
            for item in analysis.resolutions:
                if candidate_resolution_epochs[item.gap_id] > verification_gap_epochs.get(
                        item.gap_id, 0):
                    candidate_verification_gaps.pop(item.gap_id, None)
            for item in analysis.gaps:
                candidate_verification_resolved.pop(item.id, None)
            candidate_registry, candidate_owners = validate_live_projection(
                candidate_analyses, candidate_verification_gaps, runtime_gaps,
                candidate_eligible, candidate_registry, candidate_owners)
        revision = {"revision": len(history) + 1, "round": usage["rounds"],
                    "version_id": material.version_id, "duplicate": duplicate, "revisit": revisit,
                    "retrieval_receipt": deepcopy(current_return),
                    "accepted": not reasons, "analysis": asdict(analysis), "exclusion_reasons": reasons}
        if reasons:
            history.append(revision)
            event("decompose_completed", version_id=material.version_id, revision=revision["revision"])
            event("excluded_from_graph", version_id=material.version_id, reasons=reasons)
            return ()
        previous = current_analyses.get(material.version_id)
        eligible.clear()
        eligible.update(candidate_eligible)
        current_analyses.clear()
        current_analyses.update(candidate_analyses)
        analysis_epochs[material.version_id] = revision["revision"]
        analysis_resolution_epochs[material.version_id] = candidate_resolution_epochs
        verification_gaps.clear()
        verification_gaps.update(candidate_verification_gaps)
        verification_resolved.clear()
        verification_resolved.update(candidate_verification_resolved)
        gap_registry.clear()
        gap_registry.update(candidate_registry)
        gap_owners.clear()
        gap_owners.update(candidate_owners)
        history.append(revision)
        event("decompose_completed", version_id=material.version_id, revision=revision["revision"])
        event("alignment_checked", version_id=material.version_id, analysis_changed=previous != analysis)
        rebuild()
        event("graph_updated", version_id=material.version_id, open_gaps=len(gaps))
        return analysis.revisit_versions

    seen_structures = {structural_fingerprint()}
    previous_structure = structural_fingerprint()
    last_verified_input = None
    for round_number in range(1, config.max_rounds + 1):
        capacity = min(config.max_documents - usage["documents"], config.max_decomposition_calls - usage["decomposition_calls"])
        if capacity <= 0:
            stop_reason = "document_budget" if usage["documents"] >= config.max_documents else "decomposition_budget"
            break
        usage["rounds"] = round_number
        current_round_returns.clear()
        tasks = tuple(gaps.values()) or (Gap("inspect-lineage", "Inspect unresolved upstream lineage"),)
        event("search", tasks=[asdict(item) for item in tasks], limit=capacity)
        try:
            search = getattr(provider, "search_hits", None)
            if search is None:
                search = provider.search
            iterator = iter(search(target, tasks, round_number, capacity))
            # Compatibility sidecars are frozen before iteration. Lazy
            # providers must yield RetrievalHit rather than mutate this map
            # after yielding.
            round_attribution = deepcopy(getattr(provider, "last_attribution", {}) or {})
            if not isinstance(round_attribution, dict):
                raise ValueError("provider attribution must be a mapping")
        except Exception as exc:
            fail("provider", exc)
            break
        new_eligible = 0
        received = 0
        for _ in range(capacity):
            if usage["decomposition_calls"] >= config.max_decomposition_calls:
                break
            try:
                returned = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                fail("provider", exc)
                break
            if isinstance(returned, RetrievalHit):
                material = returned.material
                raw_task_ids = returned.task_ids
                attribution_kind = "envelope"
            else:
                material = returned
                raw_task_ids = round_attribution.get(
                    getattr(material, "version_id", None), ())
                attribution_kind = "legacy_sidecar" if raw_task_ids else "legacy_unattributed"
            try:
                if not isinstance(raw_task_ids, (tuple, list)) or any(
                        not isinstance(item, str) or not item for item in raw_task_ids):
                    raise ValueError("provider attribution must contain nonempty task IDs")
                if len(set(raw_task_ids)) != len(raw_task_ids):
                    raise ValueError("provider attribution contains duplicate task IDs")
                task_ids = tuple(raw_task_ids)
                issued = {task.id: task for task in tasks}
                if not set(task_ids) <= set(issued):
                    raise ValueError("provider attribution references an unissued task")
                if (round_number > 1 and not task_ids
                        and getattr(decomposer, "requires_task_attribution", False)):
                    raise ValueError("staged decomposition requires attributed round returns")
            except Exception as exc:
                fail("provider", exc)
                break
            received += 1
            usage["documents"] += 1
            try:
                reasons = _material_eligibility(material, cutoff)
                fingerprint = _fingerprint(material)
            except Exception as exc:
                fail("material", exc)
                break
            duplicate = material.version_id in materials
            if duplicate and fingerprints[material.version_id] != fingerprint:
                observations.append({"material": asdict(material), "accepted": False, "reasons": ["version_id_collision"]})
                fail("integrity", ValueError("version_id_collision: revisions require a new version_id"))
                break
            if not duplicate:
                materials[material.version_id] = material
                fingerprints[material.version_id] = fingerprint
                usage["unique_versions"] += 1
            return_record = {"version_id": material.version_id,
                             "task_ids": list(task_ids), "revisit": False,
                             "tasks": [asdict(issued[item]) for item in task_ids],
                             "attribution": attribution_kind,
                             "duplicate": duplicate, "eligible": not reasons}
            current_round_returns.append(return_record)
            event("retrieval_attribution_validated", version_id=material.version_id,
                  task_ids=list(task_ids), attribution=attribution_kind)
            observations.append({"version_id": material.version_id, "retrieved_at": material.retrieved_at,
                                 "duplicate": duplicate, "eligible": not reasons, "reasons": reasons,
                                 "task_ids": list(task_ids)})
            event("snapshot_saved" if not duplicate else "duplicate_observed", version_id=material.version_id,
                  sha256=fingerprint, eligible=not reasons)
            was_eligible = material.version_id in eligible
            try:
                # Nothing may enter the graph or verifier before this call.
                if duplicate and not config.reanalyze_existing_versions:
                    # Identity and eligibility were checked before this branch.
                    # Reuse is not a new analysis or an evidence resolution.
                    pending = []
                    event("decomposition_reused", version_id=material.version_id,
                          reason="unchanged_immutable_version", sha256=fingerprint)
                else:
                    pending = list(analyze(material, reasons, duplicate,
                                           current_return=return_record))
                if not config.reanalyze_existing_versions:
                    for version_id in pending:
                        event("reanalysis_skipped", version_id=version_id,
                              reason="unchanged_immutable_version", trigger="model_request")
                    pending = []
                # Legacy mode schedules reanalysis of an old unavailable-source
                # interpretation. Incremental mode requires the new analysis
                # to provide the explicit edge; neither mode promotes it here.
                if not reasons and not was_eligible:
                    for edge in relations.values():
                        if (edge.from_version != material.version_id and edge.from_version in eligible
                                and edge.to_version is None
                                and edge.upstream_locator in {material.url, material.version_id}
                                and edge.from_version not in pending):
                            if config.reanalyze_existing_versions:
                                pending.append(edge.from_version)
                                event("upstream_arrival_reanalysis", version_id=edge.from_version,
                                      upstream_version=material.version_id, relation_id=edge.id)
                            else:
                                event("reanalysis_skipped", version_id=edge.from_version,
                                      reason="unchanged_immutable_version", trigger="upstream_arrival",
                                      upstream_version=material.version_id, relation_id=edge.id)
                visited = {material.version_id}
                while pending:
                    version_id = pending.pop(0)
                    if version_id in visited:
                        event("revisit_cycle_detected", version_id=version_id)
                        # Already analysed in this dependency traversal. Audit the
                        # redundant request; it is not a missing news fact.
                        continue
                    if usage["decomposition_calls"] >= config.max_decomposition_calls:
                        runtime_gap = Gap(
                            "revisit:" + version_id,
                            "Reanalysis pending after decomposition budget: " + version_id)
                        candidate_runtime_gaps = dict(runtime_gaps)
                        candidate_runtime_gaps[runtime_gap.id] = runtime_gap
                        rebuild(candidate_runtime_gaps)
                        break
                    visited.add(version_id)
                    event("revisit_requested", version_id=version_id)
                    pending.extend(analyze(
                        eligible[version_id], [], True, revisit=True,
                        current_return={"version_id": version_id, "task_ids": [],
                                        "revisit": True, "duplicate": True,
                                        "eligible": True}))
            except Exception as exc:
                fail("decomposer", exc)
                break
            if reasons:
                continue
            if not was_eligible:
                new_eligible += 1
        try:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
        except Exception as exc:
            if not fatal:
                fail("provider", exc)
        if fatal:
            break
        try:
            feedback = getattr(provider, "last_feedback", ())
            if not isinstance(feedback, (tuple, list)):
                raise ValueError("provider feedback must be a list or tuple")
            _require_unique(feedback, lambda item: item.get("gap_id")
                            if isinstance(item, dict) else None,
                            "retrieval feedback gap id")
            issued = {item.id: item for item in tasks}
            attributed = {}
            for receipt in current_round_returns:
                for task_id in receipt.get("task_ids", []):
                    attributed.setdefault(task_id, set()).add(receipt["version_id"])
            validated_feedback = []
            required_fields = {"gap_id", "action", "locator", "status",
                               "reason", "version_ids"}
            for item in feedback:
                if not isinstance(item, dict) or set(item) != required_fields:
                    raise ValueError("provider feedback has missing or unexpected fields")
                gap_id = item["gap_id"]
                if gap_id not in issued:
                    raise ValueError("provider feedback references an unissued task")
                task = issued[gap_id]
                if item["action"] != task.action or item["locator"] != task.locator:
                    raise ValueError("provider feedback changes the issued task")
                if item["status"] not in {"returned", "unavailable", "budget_exhausted"}:
                    raise ValueError("invalid provider feedback status")
                if not isinstance(item["reason"], str) or not item["reason"].strip():
                    raise ValueError("provider feedback reason must be nonempty")
                version_ids = item["version_ids"]
                if (not isinstance(version_ids, list)
                        or any(not isinstance(value, str) or not value for value in version_ids)
                        or len(version_ids) != len(set(version_ids))):
                    raise ValueError("provider feedback version_ids are invalid")
                attributed_versions = attributed.get(gap_id, set())
                if item["status"] == "returned":
                    if (not attributed_versions
                            or set(version_ids) != attributed_versions):
                        raise ValueError(
                            "returned feedback must exactly match attributed returns")
                elif version_ids or attributed_versions:
                    raise ValueError(
                        "non-returned feedback conflicts with attributed returns")
                validated_feedback.append(deepcopy(item))
            strict_feedback = (round_number > 1 and (
                getattr(decomposer, "requires_task_attribution", False)
                or getattr(verifier, "uses_retrieval_feedback", False)))
            if strict_feedback and {item["gap_id"] for item in validated_feedback} != set(issued):
                raise ValueError(
                    "strict staged provider feedback must cover every issued task")
            for item in validated_feedback:
                record = {"round": round_number, **item}
                retrieval_feedback_history.append(record)
                event("retrieval_feedback", feedback=record)
        except Exception as exc:
            fail("provider", exc)
            break
        current_verifier_input = verification_input_fingerprint() if eligible else None
        fresh_verifier_evidence = (config.reanalyze_existing_versions
                                   or last_verified_input is None or new_eligible > 0)
        if (verifier is not None and eligible and fresh_verifier_evidence
                and current_verifier_input != last_verified_input):
            usage["verification_calls"] += 1
            event("verification_started")
            try:
                check = verifier.verify(target, context())
                if not isinstance(check, VerificationResult):
                    raise ValueError("verifier must return VerificationResult")
                if check.verdict not in {"supported", "contradicted", "conflicting", "unresolved"}:
                    raise ValueError("invalid verification verdict")
                for name in ("evidence_verdict", "world_verdict"):
                    if getattr(check, name) not in {None, "supported", "contradicted", "conflicting", "unresolved"}:
                        raise ValueError("invalid layered verification verdict")
                if not isinstance(check.rationale, str):
                    raise ValueError("verification rationale must be a string")
                _tuple_of(check.basis, Span, "verification basis")
                if check.verdict != "unresolved":
                    _basis(check.basis, eligible)
                    _nonempty(check.rationale, "verification rationale")
                else:
                    for item in check.basis:
                        _span(item, eligible)
                _tuple_of(check.gaps, Gap, "verification gaps")
                _tuple_of(check.resolutions, Resolution, "verification resolutions")
                for item in check.gaps:
                    _gap(item, target, eligible)
                    if item.stage != "verification":
                        raise ValueError("verifier search gaps must use stage=verification")
                    known = gap_registry.get(item.id)
                    if known is not None and known.stage == "provenance":
                        raise ValueError(f"verifier may not replace provenance gap: {item.id}")
                for item in check.resolutions:
                    _resolution(item, eligible)
                candidate_registry, candidate_owners = _register_gaps(
                    gap_registry, gap_owners, check.gaps, "verifier", target)
                _validate_resolution_references(check.resolutions, candidate_registry, "verification")
                candidate_verification_gaps = dict(verification_gaps)
                candidate_verification_gap_epochs = dict(verification_gap_epochs)
                candidate_verification_resolved = dict(verification_resolved)
                verification_epoch = len(history) + len(verifications) + 1
                for item in check.gaps:
                    candidate_verification_gaps[item.id] = item
                    candidate_verification_gap_epochs[item.id] = verification_epoch
                    candidate_verification_resolved.pop(item.id, None)
                for item in check.resolutions:
                    candidate_verification_gaps.pop(item.gap_id, None)
                    candidate_verification_resolved[item.gap_id] = item
                candidate_registry, candidate_owners = validate_live_projection(
                    current_analyses, candidate_verification_gaps, runtime_gaps,
                    eligible, candidate_registry, candidate_owners)
                effective_evidence = (check.evidence_verdict if check.evidence_verdict is not None
                                      else check.verdict)
                if effective_evidence != "unresolved":
                    _basis(check.basis, eligible)
                    _nonempty(check.rationale, "evidence verification rationale")
                    if target.evidence_scope:
                        if not set(target.evidence_scope) <= set(eligible):
                            raise ValueError("conclusive evidence judgement requires every frozen evidence_scope version")
                        if not {s.version_id for s in check.basis} <= set(target.evidence_scope):
                            raise ValueError("evidence judgement cites outside the frozen evidence_scope")
                _tuple_of(check.world_basis, Span, "world_basis")
                for span in check.world_basis:
                    _span(span, eligible)
                if check.world_verdict not in {None, "unresolved"}:
                    _basis(check.world_basis, eligible)
                    _nonempty(check.world_rationale, "world_rationale")
            except Exception as exc:
                fail("verifier", exc)
                break
            gap_registry.clear()
            gap_registry.update(candidate_registry)
            gap_owners.clear()
            gap_owners.update(candidate_owners)
            verifications.append({"round": round_number, **asdict(check)})
            verified_version_ids.update(eligible)
            verification_gaps.clear()
            verification_gaps.update(candidate_verification_gaps)
            verification_gap_epochs.clear()
            verification_gap_epochs.update(candidate_verification_gap_epochs)
            verification_resolved.clear()
            verification_resolved.update(candidate_verification_resolved)
            rebuild()
            # Include tasks/resolutions emitted by this verification so a
            # duplicate return cannot trigger a stochastic re-judgement of the
            # same effective state in the next round.
            last_verified_input = verification_input_fingerprint()
            previous_assessments = assessments
            assessments = select_assessments(check, target, gaps.values())
            fact_status = assessments["world"]["decision"]
            decision_status = assessments[target.assessment_mode]["decision"]
            event("verification_completed", verdict=fact_status, decision=decision_status,
                  assessments=deepcopy(assessments), followup_tasks=len(check.gaps),
                  provenance_status="original_material_located" if has_origin_path() and not any(
                      g.stage == "provenance" and g.blocking for g in gaps.values()) else "partial")
            if previous_assessments != assessments:
                event("assessment_changed", previous=previous_assessments, current=deepcopy(assessments),
                      basis=[asdict(s) for s in check.basis], world_basis=[asdict(s) for s in check.world_basis])
        elif verifier is not None and eligible:
            event("verification_skipped", reason=("unchanged_semantic_input"
                  if fresh_verifier_evidence else "unchanged_source_versions"))
        # Compare the frozen issue set with the post-verification active set.
        # New branches and provider-local budget deferrals are pending work,
        # even when this round returned no new eligible version.
        issued_signatures = {
            item.id: _task_signature(item, target) for item in tasks
        }
        active_signatures = {
            item.id: _task_signature(item, target) for item in gaps.values()
        }
        unissued_followups = sorted(
            gap_id for gap_id, signature in active_signatures.items()
            if issued_signatures.get(gap_id) != signature)
        feedback_by_id = {item["gap_id"]: item for item in validated_feedback}
        active_issued = {
            gap_id for gap_id, signature in active_signatures.items()
            if issued_signatures.get(gap_id) == signature
        }
        retryable_budget = sorted(
            gap_id for gap_id in active_issued
            if feedback_by_id.get(gap_id, {}).get("status") == "budget_exhausted")
        terminal_unavailable = bool(active_issued) and all(
            feedback_by_id.get(gap_id, {}).get("status") == "unavailable"
            for gap_id in active_issued)
        has_future_round = round_number < config.max_rounds
        if unissued_followups and has_future_round:
            event("followup_tasks_queued", gap_ids=unissued_followups)
        if retryable_budget and has_future_round:
            event("retrieval_budget_deferred", gap_ids=retryable_budget)
        provenance_complete = has_origin_path() and not any(item.stage == "provenance" and item.blocking for item in gaps.values())
        if provenance_complete and (verifier is None or decision_status != "unresolved") and not any(
                g.blocking and gap_dimension(g) in {"provenance", target.assessment_mode} for g in gaps.values()):
            stop_reason = "complete"
            break
        if received == 0:
            # Verification may branch from an exhausted task into a new,
            # executable task. It was not part of this round's frozen issue
            # set, so give it exactly the next bounded provider round instead
            # of declaring exhaustion before it can run.
            if unissued_followups and has_future_round:
                pass
            elif retryable_budget and has_future_round:
                pass
            elif retryable_budget:
                stop_reason = "retrieval_budget"
                break
            elif unissued_followups:
                stop_reason = "round_budget"
                break
            else:
                stop_reason = "provider_exhausted" if terminal_unavailable else "empty_results"
                break
        structure = structural_fingerprint()
        structural_progress = structure not in seen_structures
        event("progress_checked", new_eligible_versions=new_eligible, new_structure=structural_progress)
        pending_retrieval_work = has_future_round and bool(
            unissued_followups or retryable_budget)
        if new_eligible == 0 and not structural_progress and not pending_retrieval_work:
            stop_reason = "no_new_eligible_materials" if structure == previous_structure else "repeated_state"
            break
        seen_structures.add(structure)
        previous_structure = structure
        if usage["documents"] >= config.max_documents:
            stop_reason = "document_budget"
            break
        if usage["decomposition_calls"] >= config.max_decomposition_calls:
            stop_reason = "decomposition_budget"
            break
    provenance_status = "original_material_located" if has_origin_path() and not any(item.stage == "provenance" and item.blocking for item in gaps.values()) else ("partial" if eligible else "unresolved")
    if fatal:
        provenance_status = "unresolved"
    event("stopped", reason=stop_reason)
    return {
        "schema_version": "0.3", "scope": "plugin-supplied semantic analyses; no built-in truth oracle",
        "target": asdict(target), "config": asdict(config), "provenance_status": provenance_status,
        "fact_status": fact_status, "stop_reason": stop_reason, "usage": usage,
        "decision_status": decision_status, "assessments": assessments, "assessment_valid": not fatal,
        "materials": [asdict(item) for item in materials.values()],
        "eligible_version_ids": list(eligible), "observations": observations,
        "retrieval_feedback": retrieval_feedback_history,
        "analyses": {key: asdict(item) for key, item in current_analyses.items()}, "analysis_history": history,
        "fragments": [asdict(item) for item in fragments.values()],
        "relations": [asdict(item) for item in relations.values()],
        "origins": [asdict(item) for item in origins.values()],
        "gaps": [asdict(item) for item in gaps.values()],
        "resolutions": [asdict(item) for item in resolved.values()],
        "verification_history": verifications, "operations": operations, "errors": errors,
    }
