"""Run the provenance harness on local JSON snapshots without network services."""

from copy import deepcopy

from .provenance import MaterialVersion, ReplayTraceProvider, from_mapping, run_provenance


def prepare_snapshot(payload):
    """Load typed local materials shared by offline and model-backed runners."""
    if not isinstance(payload, dict) or not isinstance(payload.get("target"), dict):
        raise ValueError("local trace requires a target object")
    rounds = payload.get("rounds")
    if not isinstance(rounds, list) or any(not isinstance(items, list) for items in rounds):
        raise ValueError("local trace requires rounds as a list of material lists")
    if payload.get("config") is not None and not isinstance(payload["config"], dict):
        raise ValueError("local trace config must be an object")
    materials = []
    for items in rounds:
        batch = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError("each local material must be an object")
            batch.append(from_mapping(MaterialVersion, item, f"rounds[{len(materials)}][{index}]"))
        materials.append(batch)
    return payload["target"], ReplayTraceProvider(materials), payload.get("config")


def run_local(payload):
    """Replay local rounds; URLs are source identifiers and are never fetched."""
    target, provider, config = prepare_snapshot(payload)
    report = run_provenance(target, provider, config=config)
    # Keep the public replay report byte-for-byte faithful to the caller's
    # target object instead of exposing internal dataclass defaults.
    report["target"] = deepcopy(target)
    report["execution_mode"] = "local_snapshot_replay"
    return report
