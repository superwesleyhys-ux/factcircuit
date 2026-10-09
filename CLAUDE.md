# FactCircuit — working notes for Claude

Python 3.11+, standard library only for the core. `factcircuit` is the
canonical package and command; `newsverify` is the implementation package and
compatibility entry point. New code goes in `newsverify/` with a thin re-export
in `factcircuit/` (see `factcircuit/provenance.py`, `factcircuit/double_loop.py`).

## Before opening a PR

```bash
python -m unittest discover -s tests -v
python -m factcircuit benchmark examples/benchmark.json --output reports/benchmark.json
python scripts/release_manifest.py write   # after changing any tracked file
python scripts/release_manifest.py check
```

`RELEASE_MANIFEST.json` hashes every tracked file; CI's release job fails if it
is stale.

## Conventions

- Use `python -m factcircuit ...` in docs and examples, never `newsverify`,
  except where describing the compatibility entry point itself.
- Never present synthetic fixtures or hand-authored annotations as model
  accuracy. Keep denominators, failures, exclusions and run configuration next
  to any reported number.
- Published runs that turned out invalid stay published with the correction;
  do not delete or reword them to look better.
- Historical cutoff isolation applies to supplied evidence only; do not claim
  it removes later knowledge from a pretrained model.
- Model credentials come from the environment, never from tracked files.
