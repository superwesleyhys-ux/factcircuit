"""Controller tests; synthetic fixtures do not measure model accuracy."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'growthkit' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GrowthTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'growthkit/engine.py').is_file(), 'controller is missing')
        self.m = load('engine')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'cases.json').write_text('["a", "b"]')
        self.config = {
            'version': 1, 'objective': 'synthetic controller test', 'task_kind': 'harness',
            'case_ids': ['a', 'b'], 'initial_candidate': {'level': 0},
            'proposer': ['trusted-proposer'], 'evaluator': ['trusted-evaluator'],
            'frozen_files': ['cases.json'], 'call_budget': 30,
            'timeout_seconds': 2, 'stagnation_limit': 3, 'goal_all_pass': False,
            'limits': {'model_calls': 4, 'output_tokens': 100},
            'skill_paths': {}, 'required_skills': [],
        }
        self.write_config()
        self.calls = []

    def write_config(self):
        (self.root / 'growth.json').write_text(json.dumps(self.config))

    def report(self, request, outcomes=None, tokens=20):
        level = request['candidate']['level']
        values = outcomes if outcomes is not None else [True, level > 0]
        return {'candidate_sha256': request['candidate_sha256'],
                'case_set_sha256': request['case_set_sha256'],
                'outcomes': [{'id': key, 'passed': val} for key, val in zip(['a', 'b'], values)],
                'usage': {'model_calls': 1, 'output_tokens': tokens}}

    def runner(self, argv, request, cwd, timeout):
        self.calls.append(request)
        if request['phase'] == 'propose':
            return {'candidate': {'level': request['incumbent']['level'] + 1}}
        return self.report(request)

    def engine(self, runner=None):
        return self.m.Engine(self.root, runner=runner or self.runner)

    def test_improvement_is_promoted(self):
        state = self.engine().run(1)
        self.assertEqual(state['best'], {'level': 1})
        self.assertEqual(state['history'][-1]['decision'], 'promoted')
        self.assertEqual(state['calls_reserved'], 3)

    def test_regression_is_rejected(self):
        def runner(argv, req, cwd, timeout):
            if req['phase'] == 'propose':
                return {'candidate': {'level': 1}}
            return self.report(req, [True, False] if req['candidate']['level'] == 0 else [False, True])
        state = self.engine(runner).run(1)
        self.assertEqual(state['best'], {'level': 0})
        self.assertEqual(state['history'][-1]['reason'], 'case_regression')

    def test_equal_performance_not_promoted(self):
        def runner(argv, req, cwd, timeout):
            return {'candidate': {'level': 1}} if req['phase'] == 'propose' else self.report(req, [True, False])
        self.assertEqual(self.engine(runner).run(1)['best'], {'level': 0})

    def test_equal_passes_lower_tokens_promoted(self):
        def runner(argv, req, cwd, timeout):
            if req['phase'] == 'propose':
                return {'candidate': {'level': 1}}
            return self.report(req, [True, False], 10 if req['candidate']['level'] else 20)
        self.assertEqual(self.engine(runner).run(1)['best'], {'level': 1})

    def test_missing_case_blocks(self):
        def runner(argv, req, cwd, timeout):
            value = self.runner(argv, req, cwd, timeout)
            if req['phase'] == 'evaluate':
                value['outcomes'].pop()
            return value
        self.assertEqual(self.engine(runner).run(1)['status'], 'blocked_protocol')

    def test_duplicate_case_blocks(self):
        def runner(argv, req, cwd, timeout):
            value = self.runner(argv, req, cwd, timeout)
            if req['phase'] == 'evaluate':
                value['outcomes'][1]['id'] = 'a'
            return value
        self.assertEqual(self.engine(runner).run(1)['status'], 'blocked_protocol')

    def test_nan_and_bool_usage_block(self):
        for invalid in [float('nan'), True, -1, 1.1]:
            req = {'candidate': {'level': 0}, 'candidate_sha256': 'a', 'case_set_sha256': 'b'}
            report = self.report(req)
            report['usage']['output_tokens'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(self.m.GrowthError):
                self.m.validate_report(report, req, self.config['limits'])

    def test_report_binding_cannot_change(self):
        req = {'candidate': {'level': 0}, 'candidate_sha256': 'a', 'case_set_sha256': 'b', 'case_ids': ['a', 'b']}
        report = self.report(req)
        report['candidate_sha256'] = 'different'
        with self.assertRaises(self.m.GrowthError):
            self.m.validate_report(report, req, self.config['limits'])

    def test_per_arm_budget_violation_blocks(self):
        def runner(argv, req, cwd, timeout):
            value = self.runner(argv, req, cwd, timeout)
            if req['phase'] == 'evaluate':
                value['usage']['model_calls'] = 5
            return value
        self.assertEqual(self.engine(runner).run(1)['status'], 'blocked_protocol')

    def test_pair_receives_equal_case_set_and_limits(self):
        self.engine().run(1)
        left, right = [r for r in self.calls if r['phase'] == 'evaluate']
        for key in ['case_ids', 'case_set_sha256', 'limits', 'pair_id']:
            self.assertEqual(left[key], right[key])

    def test_lifetime_budget_does_not_reset_on_resume(self):
        self.config['call_budget'] = 3
        self.write_config()
        self.engine().run(1)
        state = self.engine().run(1)
        self.assertEqual(state['status'], 'budget_exhausted')
        self.assertEqual(len(self.calls), 3)

    def test_budget_is_reserved_before_failing_call(self):
        def fail(*args):
            raise self.m.GrowthError('blocked_auth')
        state = self.engine(fail).run(1)
        self.assertEqual(state['calls_reserved'], 1)
        self.assertEqual(state['status'], 'blocked_auth')
        self.assertEqual(state['best'], {'level': 0})

    def test_duplicate_proposal_not_reevaluated(self):
        def same(argv, req, cwd, timeout):
            self.calls.append(req)
            return {'candidate': {'level': 0}}
        state = self.engine(same).run(2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(state['history'][-1]['reason'], 'duplicate_candidate')

    def test_stagnation_stops_slice(self):
        def same(argv, req, cwd, timeout):
            return {'candidate': {'level': 0}}
        state = self.engine(same).run(10)
        self.assertEqual(state['status'], 'needs_new_hypothesis')
        self.assertEqual(state['rounds'], 3)

    def test_goal_can_finish_after_real_eval(self):
        self.config['goal_all_pass'] = True
        self.write_config()
        state = self.engine().run(3)
        self.assertEqual(state['status'], 'goal_met')
        self.assertEqual(state['rounds'], 1)
        self.engine().run(1)
        self.assertEqual(len(self.calls), 3)

    def test_stop_before_any_call(self):
        (self.root / '.growth').mkdir()
        (self.root / '.growth/STOP').touch()
        state = self.engine().run(2)
        self.assertEqual(state['status'], 'stopped')
        self.assertFalse(self.calls)

    def test_stop_between_calls_does_not_promote(self):
        def stop(argv, req, cwd, timeout):
            value = self.runner(argv, req, cwd, timeout)
            (self.root / '.growth/STOP').touch()
            return value
        state = self.engine(stop).run(2)
        self.assertEqual(state['best'], {'level': 0})
        self.assertEqual(state['calls_reserved'], 1)

    def test_existing_lock_is_preserved(self):
        lock = self.root / '.growth/LOCK'
        lock.mkdir(parents=True)
        with self.assertRaises(self.m.GrowthError):
            self.engine().run(1)
        self.assertTrue(lock.exists())

    def test_interrupted_cycle_is_not_replayed(self):
        def interrupt(*args):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.engine(interrupt).run(1)
        with self.assertRaises(self.m.GrowthError):
            self.engine().run(1)
        self.assertFalse(self.calls)

    def test_changed_contract_is_rejected(self):
        self.engine().run(1)
        self.config['call_budget'] = 1000
        self.write_config()
        with self.assertRaises(self.m.GrowthError):
            self.engine().run(1)

    def test_frozen_file_drift_is_rejected(self):
        self.engine().run(1)
        (self.root / 'cases.json').write_text('changed')
        with self.assertRaises(self.m.GrowthError):
            self.engine().run(1)

    def test_worker_asset_mutation_blocks_promotion(self):
        def mutate(argv, req, cwd, timeout):
            value = self.runner(argv, req, cwd, timeout)
            (self.root / 'cases.json').write_text('changed')
            return value
        self.assertEqual(self.engine(mutate).run(1)['status'], 'blocked_asset_drift')

    def test_missing_adapter_blocks_without_calls(self):
        self.config['proposer'] = []
        self.write_config()
        state = self.engine().run(1)
        self.assertEqual(state['status'], 'blocked_missing_adapter')
        self.assertEqual(state['calls_reserved'], 0)

    def test_unknown_task_kind_is_rejected(self):
        self.config['task_kind'] = 'unknown'
        self.write_config()
        with self.assertRaises(self.m.GrowthError):
            self.engine().run(1)

    def test_skill_discovery_is_not_assumed(self):
        skill = self.root / 'jev/SKILL.md'
        skill.parent.mkdir()
        skill.write_text('test fixture skill')
        routed = self.m.route('harness', {'jev-harness': str(skill)})
        self.assertEqual(routed['selected'], ['jev-harness'])
        self.assertIn('test-driven-development', routed['missing'])
        self.assertLessEqual(len(routed['selected']), 3)

    def test_missing_required_skill_blocks(self):
        self.config['required_skills'] = ['jev-harness']
        self.write_config()
        self.assertEqual(self.engine().run(1)['status'], 'blocked_missing_skill')

    def test_blocked_auth_does_not_automatically_retry(self):
        calls = []
        def fail(*args):
            calls.append(1)
            raise self.m.GrowthError('blocked_auth')
        self.engine(fail).run(1)
        self.engine(fail).run(1)
        self.assertEqual(len(calls), 1)

    def test_explicit_resume_preserves_charged_budget(self):
        def fail(*args):
            raise self.m.GrowthError('blocked_auth')
        self.engine(fail).run(1)
        state = self.engine().resume('Authentication independently checked and repaired')
        self.assertEqual(state['calls_reserved'], 1)
        self.assertEqual(self.engine().run(1)['calls_reserved'], 4)

    def test_gitignore_symlink_cannot_overwrite_another_file(self):
        victim = self.root / 'keep.txt'
        victim.write_text('keep')
        (self.root / '.growth').mkdir()
        (self.root / '.growth/.gitignore').symlink_to(victim)
        with self.assertRaises(self.m.GrowthError):
            self.engine().run(1)
        self.assertEqual(victim.read_text(), 'keep')

    def test_failed_evaluation_can_retry_only_after_explicit_resume(self):
        def broken(argv, req, cwd, timeout):
            return {'candidate': {'level': 1}} if req['phase'] == 'propose' else {}
        self.engine(broken).run(1)
        self.engine().resume('Evaluator repaired without changing frozen assets')
        state = self.engine().run(1)
        self.assertEqual(state['best'], {'level': 1})

    @unittest.skipUnless(os.name == 'posix', 'live runner is POSIX-only')
    def test_duplicate_json_keys_block(self):
        with self.assertRaises(self.m.GrowthError):
            self.m.run_command([sys.executable, '-c', "print('{\"candidate\":1,\"candidate\":2}')"], {}, self.root, 2)

    def test_state_cannot_escape_with_symlink(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / '.growth').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(self.m.GrowthError):
            self.engine().run(1)

    @unittest.skipUnless(os.name == 'posix', 'live runner is POSIX-only')
    def test_real_subprocess_protocol_and_resume(self):
        worker = self.root / 'worker.py'
        worker.write_text('''import json, sys\nr=json.load(sys.stdin)\nif r["phase"]=="propose":\n out={"candidate":{"level":r["incumbent"]["level"]+1}}\nelse:\n out={"candidate_sha256":r["candidate_sha256"],"case_set_sha256":r["case_set_sha256"],"outcomes":[{"id":i,"passed":True} for i in r["case_ids"]],"usage":{"model_calls":0,"output_tokens":max(0,30-r["candidate"]["level"]*10)}}\njson.dump(out,sys.stdout)\n''')
        self.config['proposer'] = self.config['evaluator'] = [sys.executable, str(worker)]
        self.config['frozen_files'].append('worker.py')
        self.write_config()
        first = self.m.Engine(self.root).run(2)
        second = self.m.Engine(self.root).run(1)
        self.assertEqual(first['best']['level'], 2)
        self.assertEqual(second['best']['level'], 3)
        self.assertEqual(second['calls_reserved'], 9)

    @unittest.skipUnless(os.name == 'posix', 'live runner is POSIX-only')
    def test_subprocess_timeout_and_invalid_json(self):
        for code in ['import time; time.sleep(4)', 'print("not json")']:
            with self.subTest(code=code), self.assertRaises(self.m.GrowthError):
                self.m.run_command([sys.executable, '-c', code], {}, self.root, 0.05)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'growthkit/install.py').is_file(), 'installer is missing')
        self.m = load('install')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_install_is_idempotent_and_preserves_model_config(self):
        (self.root / 'config.toml').write_text('model="keep-original"')
        self.m.install(ROOT, self.root)
        first = (self.root / '.agents/skills/continuous-growth/SKILL.md').read_bytes()
        self.m.install(ROOT, self.root)
        self.assertEqual(first, (self.root / '.claude/skills/continuous-growth/SKILL.md').read_bytes())
        self.assertEqual((self.root / 'config.toml').read_text(), 'model="keep-original"')

    def test_conflict_fails_before_writing(self):
        destination = self.root / '.agents/skills/continuous-growth/SKILL.md'
        destination.parent.mkdir(parents=True)
        destination.write_text('user owned')
        with self.assertRaises(self.m.InstallError):
            self.m.install(ROOT, self.root)
        self.assertEqual(destination.read_text(), 'user owned')
        self.assertFalse((self.root / 'growthkit').exists())

    def test_symlink_outside_target_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root / '.agents').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(self.m.InstallError):
                self.m.install(ROOT, self.root)


if __name__ == '__main__':
    unittest.main()
