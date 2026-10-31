from datetime import date, timedelta
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scripts import dependency_health as health


class DependencyHealthTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(health.POLICY.read_text())
        self.today = date.fromisoformat(self.policy['reviewed'])

    @unittest.skipUnless(os.environ.get('ERRAND_CHECK_DEPENDENCIES') == '1',
                         'Set ERRAND_CHECK_DEPENDENCIES=1 for live advisory checks')
    def test_live_dependency_health(self):
        self.assertEqual(health.main(['--online']), 0,
                         '依存の脆弱性・ライセンス変更・未確認項目を確認してください。')

    def test_security_policy_is_current_and_fixed_build_dependencies_reviewed(self):
        _, unknown = health.evaluate([], self.policy)
        self.assertFalse(unknown, '依存ポリシーを公式情報で見直してください。')
        rows = health.build_packages(self.policy)
        self.assertEqual({p['name'] for p in rows}, set(self.policy['build_dependencies']))
        problems, unknown = health.evaluate(rows, self.policy)
        self.assertFalse(problems or unknown)
        manifest = json.loads((health.ROOT / 'errand/runtime_manifest.json').read_text())
        self.assertGreaterEqual(health.semver(manifest['version']),
                                health.semver(self.policy['cli_minimums']['@openai/codex']))

    def test_old_node_and_cli_fail_and_new_node_passes(self):
        def package(name, version):
            return {'name': name, 'version': version, 'ecosystem': 'npm', 'license': 'Apache-2.0'}
        for name, version in [('Node.js', '24.13.1'), ('@google/gemini-cli', '0.39.0'),
                              ('@openai/codex', '0.38.0'), ('@openai/codex', '0.39.0-alpha.1')]:
            self.assertTrue(health.evaluate([package(name, version)], self.policy, self.today)[0])
        self.assertFalse(health.evaluate([package('Node.js', '24.22.2')], self.policy, self.today)[0])
        self.assertGreater(health.semver('0.166.0-alpha.5.1'), health.semver('0.39.0'))

    def test_unknown_node_license_change_and_expired_policy_are_not_green(self):
        row = {'name': 'Node.js', 'version': '99.1.0', 'license': 'MIT'}
        self.assertTrue(health.evaluate([row], self.policy, self.today)[1])
        row = {'name': '@google/gemini-cli', 'version': '0.66.0', 'ecosystem': 'npm', 'license': 'changed'}
        self.assertTrue(health.evaluate([row], self.policy, self.today)[0])
        expired = self.today + timedelta(days=self.policy['review_after_days'] + 1)
        self.assertTrue(health.evaluate([], self.policy, expired)[1])

    def test_ubuntu_versions_are_not_compared_to_upstream_pypi_versions(self):
        row = {'name': 'pycairo', 'version': '1.27.0-2build2', 'ecosystem': 'Ubuntu:26.04:LTS',
               'license': 'distribution copyright'}
        self.assertEqual(health.evaluate([row], self.policy, self.today), ([], []))
        fetch = Mock(return_value={'results': [{}]})
        self.assertEqual(health.osv_findings([row], fetch), [])
        query = fetch.call_args.args[1]['queries'][0]
        self.assertEqual(query['package']['ecosystem'], 'Ubuntu:26.04:LTS')
        self.assertEqual(query['version'], '1.27.0-2build2')

    def test_osv_pagination_is_complete_and_empty_results_are_valid(self):
        row = {'name': 'example', 'version': '1.0', 'ecosystem': 'PyPI'}
        fetch = Mock(side_effect=[{'results': [{'vulns': [{'id': 'CVE-first'}], 'next_page_token': 'next'}]},
                                  {'results': [{'vulns': [{'id': 'CVE-second'}]}]}])
        result = health.osv_findings([row], fetch)
        self.assertEqual(result[0][1], ['CVE-first', 'CVE-second'])
        self.assertEqual(fetch.call_args.args[1]['queries'][0]['page_token'], 'next')
        for bad in ({}, {'results': []}, {'results': [None]}, {'results': [{'vulns': None}]},
                    {'results': [{'error': 'unknown ecosystem'}]}, {'results': [{'next_page_token': 0}]}):
            with self.assertRaises((ValueError, TypeError)):
                health.osv_findings([row], lambda *_: bad)

    def test_online_node_uses_current_security_releases_not_only_static_floor(self):
        rows = [{'name': 'Node.js', 'version': '24.22.2'}]
        fetch = lambda _: [{'version': 'v24.23.1', 'security': True}]
        self.assertTrue(health.node_findings(rows, fetch))
        with self.assertRaises(ValueError):
            health.node_findings(rows, lambda _: [])

    def test_network_failure_returns_unverified_not_success(self):
        with patch.object(health, 'python_packages', return_value=[]), \
                patch.object(health, 'ubuntu_packages', return_value=[]), \
                patch.object(health, 'cli_packages', return_value=([], [])), \
                patch.object(health, 'osv_findings', side_effect=OSError('offline')), \
                patch.object(health, 'node_findings', return_value=[]), \
                patch.object(health, 'build_license_findings', return_value=[]), \
                patch('builtins.print'):
            self.assertEqual(health.main(['--online']), 2)

    def test_advisory_and_license_change_return_failure(self):
        rows = health.build_packages(self.policy)
        response = lambda _: {'info': {'license': 'different'}}
        self.assertTrue(health.build_license_findings(rows, response))
        with patch.object(health, 'python_packages', return_value=[]), \
                patch.object(health, 'ubuntu_packages', return_value=[]), \
                patch.object(health, 'cli_packages', return_value=([], [])), \
                patch.object(health, 'osv_findings', return_value=[({'package': {'name': 'test'}, 'version': '1'}, ['CVE-test'])]), \
                patch.object(health, 'node_findings', return_value=[]), \
                patch.object(health, 'build_license_findings', return_value=[]), \
                patch('builtins.print'):
            self.assertEqual(health.main(['--online']), 1)
