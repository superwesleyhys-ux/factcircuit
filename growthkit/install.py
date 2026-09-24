"""Install only GrowthKit-owned files; never overwrite a different installation."""
from __future__ import annotations

import argparse
from pathlib import Path


class InstallError(RuntimeError):
    pass


def install(source_root, project):
    source, target = Path(source_root).resolve(strict=True), Path(project).resolve(strict=True)
    if not target.is_dir():
        raise InstallError('Target must be an existing project directory')
    names = ['engine.py', 'install.py', 'SKILL.md', 'README.md']
    files = {f'growthkit/{name}': source / 'growthkit' / name for name in names}
    for host in ('.agents', '.claude'):
        files[f'{host}/skills/continuous-growth/SKILL.md'] = source / 'growthkit/SKILL.md'
    # Complete preflight before creating any target directories.
    for relative, origin in files.items():
        destination = target / relative
        if not origin.is_file():
            raise InstallError(f'Missing source: {relative}')
        if not destination.resolve().is_relative_to(target) or destination.is_symlink():
            raise InstallError(f'Unsafe destination: {relative}')
        if destination.exists() and (not destination.is_file() or destination.read_bytes() != origin.read_bytes()):
            raise InstallError(f'Existing different file: {relative}')
        for parent in destination.parents:
            if parent == target:
                break
            if parent.exists() and not parent.is_dir():
                raise InstallError(f'Parent is not a directory: {relative}')
    created = []
    for relative, origin in files.items():
        destination = target / relative
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects against a concurrent install/change.
        try:
            with destination.open('xb') as handle:
                handle.write(origin.read_bytes())
        except FileExistsError as exc:
            raise InstallError(f'Destination appeared concurrently: {relative}') from exc
        created.append(relative)
    return {'created': created, 'model_config_changed': False, 'live_inference_tested': False}


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(install(Path(__file__).resolve().parents[1], args.project)))
    except (InstallError, OSError) as exc:
        parser.exit(2, f'Install blocked: {exc}\n')


if __name__ == '__main__':
    main()
