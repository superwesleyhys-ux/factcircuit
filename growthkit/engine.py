"""Durable outer harness evolution; no model, scheduler or provider is enabled.

Commands are trusted adapters, NOT sandboxed. See README for the trust boundary.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

MAX_RESPONSE = 1_000_000
ROUTES = {
    'harness': ('jev-harness', 'test-driven-development', 'verification-before-completion'),
    'debug': ('systematic-debugging', 'test-driven-development', 'jev-harness'),
    'research': ('autoresearch', 'jev-harness', 'verification-before-completion'),
    'code': ('ultragoal', 'test-driven-development', 'verification-before-completion'),
    'review': ('code-review', 'systematic-debugging', 'verification-before-completion'),
}


class GrowthError(RuntimeError):
    """A public, credential-free blocking reason."""


def require(condition, reason='blocked_protocol'):
    if not condition:
        raise GrowthError(reason)


def digest(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise GrowthError('blocked_protocol') from exc
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'blocked_duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def read_json(path):
    try:
        return strict_json(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise GrowthError('blocked_invalid_json') from exc


def atomic_json(path, value):
    path = Path(path)
    require(not path.is_symlink(), 'blocked_symlink')
    descriptor, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def route(task_kind, paths):
    require(task_kind in ROUTES, 'blocked_unknown_task_kind')
    selected, missing = [], []
    for name in ROUTES[task_kind]:
        location = paths.get(name)
        path = Path(location).expanduser() if isinstance(location, str) and location else None
        if path is not None and (path.is_file() or (path / 'SKILL.md').is_file()):
            selected.append(name)
        else:
            missing.append(name)
    return {'selected': selected, 'missing': missing}


def run_command(argv, request, cwd, timeout):
    """One bounded POSIX process group. No shell interpolation or raw output log."""
    require(os.name == 'posix', 'blocked_unsupported_platform')
    payload = json.dumps(request, allow_nan=False).encode()
    require(len(payload) <= MAX_RESPONSE, 'blocked_request_too_large')
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                       stdout=output, stderr=errors, start_new_session=True)
        except OSError as exc:
            raise GrowthError('blocked_missing_adapter') from exc
        try:
            process.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise GrowthError('blocked_timeout') from exc
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        require(process.returncode == 0, 'blocked_adapter_exit')
        output.seek(0)
        raw = output.read(MAX_RESPONSE + 1)
        require(len(raw) <= MAX_RESPONSE, 'blocked_response_too_large')
        try:
            result = strict_json(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GrowthError('blocked_protocol') from exc
        require(isinstance(result, dict))
        if result.get('status') == 'blocked':
            reason = result.get('reason')
            require(reason in {'auth', 'quota', 'permission', 'capability'})
            raise GrowthError('blocked_' + reason)
        return result


def validate_report(report, request, limits):
    require(isinstance(report, dict) and set(report) == {
        'candidate_sha256', 'case_set_sha256', 'outcomes', 'usage'})
    require(report['candidate_sha256'] == request['candidate_sha256'])
    require(report['case_set_sha256'] == request['case_set_sha256'])
    outcomes, usage = report['outcomes'], report['usage']
    require(isinstance(outcomes, list) and isinstance(usage, dict))
    require(set(usage) == {'model_calls', 'output_tokens'})
    for key, cap in limits.items():
        require(type(usage.get(key)) is int and 0 <= usage[key] <= cap)
    require('case_ids' in request)
    ids = []
    for item in outcomes:
        require(isinstance(item, dict) and set(item) == {'id', 'passed'})
        require(isinstance(item['id'], str) and type(item['passed']) is bool)
        ids.append(item['id'])
    require(len(ids) == len(set(ids)) and set(ids) == set(request['case_ids']))
    return deepcopy(report)


def select(incumbent, candidate):
    old = {x['id']: x['passed'] for x in incumbent['outcomes']}
    new = {x['id']: x['passed'] for x in candidate['outcomes']}
    if any(passed and not new[key] for key, passed in old.items()):
        return False, 'case_regression'
    if sum(new.values()) > sum(old.values()):
        return True, 'more_passing_cases'
    if (candidate['usage']['output_tokens'] < incumbent['usage']['output_tokens']
            and candidate['usage']['model_calls'] <= incumbent['usage']['model_calls']):
        return True, 'same_passes_fewer_output_tokens'
    return False, 'no_measured_improvement'


class Engine:
    def __init__(self, project, *, runner=run_command):
        self.root = Path(project).resolve(strict=True)
        self.home = self.root / '.growth'
        self.config_path = self.root / 'growth.json'
        self.config = read_json(self.config_path)
        self.runner = runner
        self._validate_config()
        self.contract_sha = digest(self.config)
        self.controller_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.assets = self._assets()
        self.routing = route(self.config['task_kind'], self.config.get('skill_paths', {}))

    def _validate_config(self):
        c = self.config
        require(isinstance(c, dict) and type(c.get('version')) is int and c['version'] == 1)
        require(isinstance(c.get('objective'), str) and bool(c['objective'].strip()))
        require(c.get('task_kind') in ROUTES, 'blocked_unknown_task_kind')
        ids = c.get('case_ids')
        require(isinstance(ids, list) and bool(ids) and all(isinstance(x, str) and x for x in ids))
        require(len(set(ids)) == len(ids))
        require(isinstance(c.get('initial_candidate'), dict) and bool(c['initial_candidate']))
        for key in ('proposer', 'evaluator'):
            command = c.get(key)
            require(isinstance(command, list) and all(isinstance(x, str) and x for x in command))
        for key in ('call_budget', 'timeout_seconds', 'stagnation_limit'):
            require(type(c.get(key)) is int and c[key] > 0)
        limits = c.get('limits')
        require(isinstance(limits, dict) and set(limits) == {'model_calls', 'output_tokens'})
        require(all(type(x) is int and x >= 0 for x in limits.values()))
        require(type(c.get('goal_all_pass', False)) is bool)
        require(isinstance(c.get('skill_paths', {}), dict))
        require(isinstance(c.get('required_skills', []), list))
        require(all(isinstance(x, str) for x in c.get('required_skills', [])))
        require(isinstance(c.get('frozen_files'), list) and bool(c['frozen_files']))
        require(all(isinstance(x, str) and x for x in c['frozen_files']))
        digest(c)

    def _assets(self):
        result = {}
        for name in self.config['frozen_files']:
            path = (self.root / name).resolve()
            require(path.is_relative_to(self.root) and path.is_file(), 'blocked_frozen_path')
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def _check_frozen(self):
        require(digest(read_json(self.config_path)) == self.contract_sha, 'blocked_contract_drift')
        require(self._assets() == self.assets, 'blocked_asset_drift')
        require(digest(read_json(self.home / 'state.json')) == self.saved_state_sha, 'blocked_state_drift')

    def _save(self):
        atomic_json(self.home / 'state.json', self.state)
        self.saved_state_sha = digest(self.state)

    def _load(self):
        path = self.home / 'state.json'
        if path.exists():
            state = read_json(path)
            require(isinstance(state, dict), 'blocked_state')
            require(state.get('project_root') == str(self.root), 'blocked_project_mismatch')
            require(state.get('contract_sha256') == self.contract_sha, 'blocked_contract_drift')
            require(state.get('controller_sha256') == self.controller_sha, 'blocked_controller_drift')
            require(state.get('assets') == self.assets, 'blocked_asset_drift')
            require(state.get('best_sha256') == digest(state.get('best')), 'blocked_state')
            require(state.get('inflight') is None, 'blocked_interrupted_cycle')
            require(type(state.get('calls_reserved')) is int and state['calls_reserved'] >= 0, 'blocked_state')
            require(type(state.get('rounds')) is int and state['rounds'] >= 0, 'blocked_state')
            require(type(state.get('stagnant')) is int and state['stagnant'] >= 0, 'blocked_state')
            require(isinstance(state.get('history'), list) and isinstance(state.get('seen'), list), 'blocked_state')
            return state
        return {'version': 1, 'project_root': str(self.root), 'contract_sha256': self.contract_sha,
                'controller_sha256': self.controller_sha, 'assets': self.assets,
                'best': deepcopy(self.config['initial_candidate']),
                'best_sha256': digest(self.config['initial_candidate']), 'calls_reserved': 0,
                'rounds': 0, 'stagnant': 0, 'inflight': None,
                'seen': [digest(self.config['initial_candidate'])], 'history': [], 'status': 'ready'}

    def _call(self, phase, request):
        require(not (self.home / 'STOP').exists(), 'stopped')
        self._check_frozen()
        require(self.state['calls_reserved'] < self.config['call_budget'], 'budget_exhausted')
        self.state['calls_reserved'] += 1
        self.state['inflight']['phase'] = phase
        self._save()  # Reserve before side effects; a crash never refunds the call.
        command = self.config['proposer' if phase == 'propose' else 'evaluator']
        value = self.runner(command, deepcopy(request), self.root, self.config['timeout_seconds'])
        self._check_frozen()
        require(isinstance(value, dict))
        require(not (self.home / 'STOP').exists(), 'stopped')
        return value

    def resume(self, reason):
        require(isinstance(reason, str) and 10 <= len(reason.strip()) <= 500, 'blocked_resume_reason')
        return self.run(1, _resume_reason=reason.strip())

    def run(self, cycles=1, *, _resume_reason=None):
        require(type(cycles) is int and 1 <= cycles <= 20, 'blocked_cycle_limit')
        require(not self.home.is_symlink(), 'blocked_symlink')
        self.home.mkdir(exist_ok=True)
        lock = self.home / 'LOCK'
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise GrowthError('blocked_active_or_stale_lock') from exc
        try:
            ignore = self.home / '.gitignore'
            require(not ignore.is_symlink(), 'blocked_symlink')
            if not ignore.exists():
                with ignore.open('x', encoding='utf-8') as handle:
                    handle.write('*\n')
            atomic_json(lock / 'owner.json', {'pid': os.getpid(), 'started_at': time.time()})
            self.state = self._load()
            self._save()
            if _resume_reason is not None:
                require(self.state['status'] not in {'goal_met', 'budget_exhausted'}, 'blocked_terminal_goal')
                self.state.setdefault('acknowledgements', []).append({
                    'previous_status': self.state['status'], 'reason': _resume_reason,
                    'at_call_reservation': self.state['calls_reserved']})
                self.state['status'], self.state['stagnant'] = 'ready', 0
                self._save()
                return deepcopy(self.state)
            for _ in range(cycles):
                if not self._cycle():
                    break
            self._save()
            return deepcopy(self.state)
        finally:
            (lock / 'owner.json').unlink(missing_ok=True)
            lock.rmdir()

    def _cycle(self):
        s, c = self.state, self.config
        if s['status'] == 'goal_met' or s['status'].startswith('blocked_'):
            return False
        if (self.home / 'STOP').exists():
            s['status'] = 'stopped'
            return False
        if not c['proposer'] or not c['evaluator']:
            s['status'] = 'blocked_missing_adapter'
            return False
        if any(name not in self.routing['selected'] for name in c.get('required_skills', [])):
            s['status'] = 'blocked_missing_skill'
            return False
        if s['stagnant'] >= c['stagnation_limit']:
            s['status'] = 'needs_new_hypothesis'
            return False
        if c['call_budget'] - s['calls_reserved'] < 3:
            s['status'] = 'budget_exhausted'
            return False
        pair_id = digest({'contract': self.contract_sha, 'round': s['rounds'] + 1})
        s['inflight'] = {'pair_id': pair_id, 'phase': 'reserved'}
        self._save()
        entry = {'round': s['rounds'] + 1, 'pair_id': pair_id,
                 'parent_sha256': s['best_sha256'], 'decision': 'rejected'}
        try:
            result = self._call('propose', {'phase': 'propose', 'objective': c['objective'],
                'incumbent': s['best'], 'incumbent_sha256': s['best_sha256'],
                'routing': self.routing, 'feedback': s['history'][-3:],
                'limits': c['limits'], 'pair_id': pair_id})
            require(set(result) == {'candidate'} and isinstance(result['candidate'], dict) and bool(result['candidate']))
            candidate = result['candidate']
            candidate_sha = digest(candidate)
            entry['candidate_sha256'] = candidate_sha
            if candidate_sha in s['seen']:
                entry['reason'] = 'duplicate_candidate'
                s['stagnant'] += 1
            else:
                case_sha = digest({'case_ids': c['case_ids'], 'assets': self.assets})
                reports = []
                for artifact in (s['best'], candidate):
                    request = {'phase': 'evaluate', 'candidate': artifact,
                        'candidate_sha256': digest(artifact), 'case_ids': c['case_ids'],
                        'case_set_sha256': case_sha, 'limits': c['limits'], 'pair_id': pair_id}
                    report = self._call('evaluate', request)
                    reports.append(validate_report(report, request, c['limits']))
                s['seen'].append(candidate_sha)
                promoted, entry['reason'] = select(*reports)
                entry['evaluations'] = reports
                atomic_json(self.home / f'candidate-{candidate_sha}.json', candidate)
                if promoted:
                    s['best'], s['best_sha256'] = candidate, candidate_sha
                    s['stagnant'] = 0
                    entry['decision'] = 'promoted'
                else:
                    s['stagnant'] += 1
                chosen_report = reports[1] if promoted else reports[0]
                if c.get('goal_all_pass', False) and all(x['passed'] for x in chosen_report['outcomes']):
                    s['status'] = 'goal_met'
            if s['status'] != 'goal_met':
                s['status'] = 'ready'
        except GrowthError as exc:
            s['status'] = str(exc)
            entry['reason'] = s['status']
        except Exception:
            # Do not echo arbitrary adapter exception text: it may contain secrets.
            s['status'] = entry['reason'] = 'blocked_adapter_error'
        s['rounds'] += 1
        s['history'].append(entry)
        s['inflight'] = None
        if s['stagnant'] >= c['stagnation_limit'] and s['status'] == 'ready':
            s['status'] = 'needs_new_hypothesis'
        self._save()
        return s['status'] == 'ready'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'status', 'resume'])
    parser.add_argument('--project', default='.')
    parser.add_argument('--cycles', type=int, default=1)
    parser.add_argument('--reason', default='')
    args = parser.parse_args()
    try:
        if args.action == 'status':
            path = Path(args.project) / '.growth/state.json'
            state = read_json(path) if path.exists() else {'status': 'not_initialized'}
        elif args.action == 'resume':
            state = Engine(args.project).resume(args.reason)
        else:
            state = Engine(args.project).run(args.cycles)
        summary = {key: state[key] for key in ('status', 'rounds', 'calls_reserved', 'best_sha256') if key in state}
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if state['status'] in {'ready', 'goal_met', 'not_initialized'} else 2
    except GrowthError as exc:
        print(json.dumps({'status': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
