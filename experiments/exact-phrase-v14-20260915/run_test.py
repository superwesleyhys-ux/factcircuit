"""Frozen, equal-access diagnostic of literal tracing on one real news event.

This is a public-archive replay with REAL model inference, not a live unrestricted
search benchmark or an unseen truth-accuracy benchmark. No outcome labels load.
"""
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments/live-origin-v12-20260914"))
from archive_collector import ArchiveCollector
from newsverify.phrase_sources import LiteralDocument, LiteralSourceCollector
from newsverify.phrase_tracing import run_phrase_trace, DIRECT, HARNESS
from newsverify.phrase_spans import text_sha256
from newsverify.tunnels import LocalTunnel

CUTOFF = "2024-12-31T23:59:59Z"
SEED = "https://www.axios.com/2022/10/11/nasa-dart-asteroid-deflection"
MODEL, EFFORT = "gpt-6-astra", "low"
CACHE = ROOT / "private-data/v14-sources/cache"


def dump(path, value):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class ArchivedLiteral(LiteralDocument):
    capture_at: str = ""
    archive_url: str = ""

    @property
    def available_at(self):
        return self.capture_at

    @property
    def availability_basis(self):
        return "Archive Memento-Datetime and original identity validated: " + self.archive_url


class FrozenArchive(LiteralSourceCollector):
    """Fetch only the two preregistered raw captures, on demand per model action.

    Every arm and every item starts with the same URL-accessible pool; only the
    seed is initially in its model packet. No archive cache expands during inference.
    """
    def __init__(self, **limits):
        registration_path = HERE / "REGISTRATION.json"
        self.registered = (json.loads(registration_path.read_text())["raw_captures"]
                           if registration_path.exists() else
                           {str(p.relative_to(ROOT)): sha(p) for p in CACHE.glob("*")})
        self.reader = ArchiveCollector(cutoff=CUTOFF, cache_dir=CACHE, max_searches=0)
        super().__init__(fetch=self._raw, **limits)

    def _raw(self, url):
        key = hashlib.sha256((CUTOFF + "\n" + url).encode()).hexdigest()
        for suffix in (".json", ".body"):
            path = CACHE / (key + suffix)
            relative = str(path.relative_to(ROOT))
            if relative not in self.registered or not path.exists():
                raise ValueError("outside_registered_source_pool")
            if sha(path) != self.registered[relative]:
                raise ValueError("registered_source_changed")
        return self.reader._archive_fetch(url)

    def fetch(self, url):
        doc = super().fetch(url)
        if doc is None or isinstance(doc, ArchivedLiteral):
            return doc
        meta = self.reader.archive_records[url]
        values = dict(doc.__dict__, retrieved_at=meta["retrieved_at"])
        doc = ArchivedLiteral(**values, capture_at=meta["capture_at"], archive_url=meta["archive_url"])
        self.documents[doc.url] = doc
        return doc

    def search(self, query, limit=2):
        self.requests.append(dict(operation="search", query=query, success=False, error="search_not_registered"))
        self._error("search", "search_not_registered")
        return []


class Journal:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir()
        self.base = LocalTunnel(model=MODEL, reasoning_effort=EFFORT, timeout=180,
                                diagnostic_directory=directory / "cli")
        self.calls = []
        self.entries = []

    def generate(self, stage, instructions, packet, schema):
        entry = dict(stage=stage, started_at=datetime.now(timezone.utc).isoformat(),
                     instructions_sha256=text_sha256(instructions), packet=packet, status="started")
        self.entries.append(entry)
        dump(self.directory / "journal.json", self.entries)
        print(json.dumps(dict(arm=self.directory.name, call=len(self.entries), item=packet["item"]["text"])), flush=True)
        try:
            value = self.base.generate(stage, instructions, packet, schema)
            entry.update(status="completed", response=value)
            return value
        except Exception as exc:
            entry.update(status="failed", error=str(exc))
            raise
        finally:
            self.calls = list(self.base.calls)
            entry["finished_at"] = datetime.now(timezone.utc).isoformat()
            dump(self.directory / "journal.json", self.entries)
            dump(self.directory / "calls.json", self.calls)


def freeze():
    collector = FrozenArchive(max_documents=4, max_searches=0, max_chars=200000)
    seed = collector.fetch(SEED)
    if seed is None:
        raise ValueError("seed unavailable")
    conditional = seed.content.index("if", seed.content.index("The test would"))
    selectors = ["32 minutes", "73 seconds", {"start": conditional, "end": conditional + 2}]
    payload = dict(url=SEED, selectors=selectors, as_of=CUTOFF,
                   limits=dict(max_calls=3, max_documents=4, max_searches=0, max_chars=200000, context_chars=160))
    dump(HERE / "INPUT.json", payload)
    files = [*sorted((ROOT / "newsverify").glob("*.py")),
             ROOT / "experiments/live-origin-v12-20260914/archive_collector.py",
             HERE / "run_test.py", HERE / "PROTOCOL.md", HERE / "INPUT.json"]
    raw = sorted(CACHE.glob("*"))
    registration = dict(created_at=datetime.now(timezone.utc).isoformat(), model=MODEL, reasoning_effort=EFFORT,
                        input_sha256=text_sha256(seed.content), raw_captures={str(p.relative_to(ROOT)): sha(p) for p in raw},
                        files={str(p.relative_to(ROOT)): sha(p) for p in files},
                        policy_sha256=dict(direct=text_sha256(DIRECT), harness=text_sha256(HARNESS)),
                        planned_items=3, max_calls_total=18, arm_order=["direct", "harness"],
                        later_outcomes_loaded=False, no_model_calls_during_freeze=True)
    dump(HERE / "REGISTRATION.json", registration)
    return registration


def verify(registration):
    if {str(p.relative_to(ROOT)) for p in CACHE.glob("*")} != set(registration["raw_captures"]):
        raise ValueError("Registered source pool file set changed")
    for path, expected in (registration["files"] | registration["raw_captures"]).items():
        if sha(ROOT / path) != expected:
            raise ValueError("Frozen input/code changed: " + path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.freeze:
        if (HERE / "REGISTRATION.json").exists():
            raise ValueError("Do not overwrite registration")
        freeze()
        print("Registered 3 literal occurrences, 2 arms, at most 18 real calls; no inference yet.")
        return
    if args.output is None:
        raise ValueError("new output directory required")
    args.output.mkdir(parents=True, exist_ok=False)
    registration = json.loads((HERE / "REGISTRATION.json").read_text())
    verify(registration)
    payload = json.loads((HERE / "INPUT.json").read_text())
    for arm in registration["arm_order"]:
        journal = Journal(args.output / arm)
        result = run_phrase_trace(payload, arm=arm, transport=journal, collector_factory=FrozenArchive)
        dump(args.output / (arm + ".json"), result)
        verify(registration)
    dump(args.output / "COMPLETION.json", dict(status="completed", finished_at=datetime.now(timezone.utc).isoformat(),
                                               registration_sha256=sha(HERE / "REGISTRATION.json"), frozen_hashes_match=True))


if __name__ == "__main__":
    main()
