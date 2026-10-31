#!/usr/bin/env python3
"""Dependency inventory and fail-closed advisory checks; never reads credentials."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chat_foundation.gemini.network import HTTPSHandler

POLICY = ROOT / 'scripts/dependency-policy.json'
UBUNTU_PACKAGES = ('libgtk-4-1', 'libadwaita-1-0', 'libglib2.0-0t64', 'libpango-1.0-0',
                   'libcairo2', 'libsecret-1-0', 'python3-gi', 'python3-cairo',
                   f'python{sys.version_info.major}.{sys.version_info.minor}', 'libssl3t64')


def semver(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?', value)
    if not match:
        raise ValueError('unsupported version format')
    prerelease = match[4]
    suffix = (1,) if prerelease is None else (0, tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split('.')))
    return tuple(map(int, match.groups()[:3])) + (suffix,)


def evaluate(packages, policy, today=None):
    problems, unknown = [], []
    age = ((today or date.today()) - date.fromisoformat(policy['reviewed'])).days
    if age < 0 or age > policy['review_after_days']:
        unknown.append('依存ポリシーの見直し期限を過ぎています。')
    for package in packages:
        name, version = package['name'], package['version']
        if not package.get('license'):
            unknown.append(f'{name}: ライセンス情報を取得できません。')
        accepted = policy['direct_licenses'].get(name) if package.get('ecosystem') in ('npm', 'PyPI') else None
        if accepted and package.get('license') not in accepted:
            problems.append(f'{name}: ライセンスが確認済みの値と異なります。')
        minimum = policy['cli_minimums'].get(name)
        if name == 'Node.js':
            minimum = policy['node_security_floors'].get(str(semver(version)[0]))
            if minimum is None:
                unknown.append(f'Node.js {version}: 未確認のリリース系列です。')
        if minimum and semver(version) < semver(minimum):
            problems.append(f'{name} {version}: 確認済みのセキュリティ修正版 {minimum} より古い版です。')
    return problems, unknown


def command(args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def python_packages():
    rows = []
    for name in ('PyGObject', 'pycairo'):
        metadata = importlib.metadata.metadata(name)
        rows.append({'name': name, 'version': metadata['Version'], 'ecosystem': 'PyPI',
                     'license': metadata.get('License-Expression') or metadata.get('License')})
    return rows


def build_packages(policy):
    rows = []
    for line in (ROOT / 'scripts/build-requirements.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([0-9A-Za-z.+-]+)', line)
        if not match:
            raise ValueError('build dependency must be pinned')
        name, version = match.groups()
        reviewed = policy['build_dependencies'].get(name, {})
        rows.append({'name': name, 'version': version, 'ecosystem': 'PyPI',
                     'scope': 'pinned build dependency; not necessarily installed',
                     'license': reviewed.get('license') if version == reviewed.get('version') else None})
    return rows


def ubuntu_packages():
    release = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            release[key] = value.strip('"')
    if release.get('ID') != 'ubuntu':
        raise ValueError('このOSのネイティブライブラリ照合は未対応です。')
    output = command(['dpkg-query', '-W', '-f=${source:Package}\t${source:Version}\t${binary:Package}\n',
                      *UBUNTU_PACKAGES])
    rows = []
    for line in output.splitlines():
        source, version, binary = line.split('\t')
        notice = Path('/usr/share/doc') / binary.split(':')[0] / 'copyright'
        rows.append({'name': source, 'version': version, 'ecosystem': 'Ubuntu:' + release['VERSION_ID'] + ':LTS',
                     'license': '配布パッケージのcopyrightを参照' if notice.is_file() else None})
    return rows


def cli_packages():
    from errand.settings import Settings
    from errand.protocol import find_codex
    from errand.gemini_cli import find_gemini
    settings = Settings()
    if settings.error:
        raise ValueError('Errandの設定を読み込めないため、実際のCLIを特定できません。')
    rows, nodes, notes = [], set(), []
    for find, key in ((find_codex, 'codex-path'), (find_gemini, 'gemini-cli-path')):
        try:
            executable = Path(find(settings.get(key) or None))
        except RuntimeError:
            notes.append(f'{key}: 未導入のため対象外です。')
            continue
        # Resolve package metadata only; do not start the agent or load its credentials.
        metadata_file = next((parent / 'package.json' for parent in executable.resolve().parents
                              if (parent / 'package.json').is_file()), None)
        if metadata_file is None:
            if key != 'codex-path':
                raise ValueError(f'{key}: npmパッケージ情報がなく、別途確認が必要です。')
            with tempfile.TemporaryDirectory(prefix='errand-health-version-') as directory:
                result = subprocess.run([str(executable), '--version'], text=True, capture_output=True, timeout=30,
                                        env={'HOME': directory, 'CODEX_HOME': directory, 'PATH': '/usr/bin:/bin'})
            match = re.fullmatch(r'codex-cli (\S+)\s*', result.stdout)
            if result.returncode or not match:
                raise ValueError('ネイティブCodexのバージョンを確認できません。')
            rows.append({'name': '@openai/codex', 'version': match[1], 'ecosystem': 'npm',
                         'license': 'Apache-2.0', 'license_source': 'upstream declaration'})
            notes.append('ネイティブCodexのライセンスは上流の宣言を使用。配布物の通知ファイルは別途照合してください。')
            continue
        metadata = json.loads(metadata_file.read_text())
        rows.append({'name': metadata['name'], 'version': metadata['version'], 'ecosystem': 'npm',
                     'license': metadata.get('license')})
        modules = metadata_file.parent / 'node_modules'
        for path in modules.rglob('package.json') if modules.is_dir() else ():
            parent = path.parent
            if parent.parent.name != 'node_modules' and not (
                    parent.parent.name.startswith('@') and parent.parent.parent.name == 'node_modules'):
                continue
            item = json.loads(path.read_text())
            if item.get('name') and item.get('version'):
                rows.append({'name': item['name'], 'version': item['version'], 'ecosystem': 'npm',
                             'license': item.get('license')})
        node = executable.parent / 'node'
        if not node.is_file():
            import shutil
            found = shutil.which('node')
            if not found:
                raise ValueError('CLI用のNode.jsが見つかりません。')
            node = Path(found)
        if node.resolve() not in nodes:
            nodes.add(node.resolve())
            rows.append({'name': 'Node.js', 'version': command([str(node), '--version']).lstrip('v'),
                         'license': 'MIT and bundled third-party licenses', 'ecosystem': None})
    notes.append('CLIにバンドルされた依存のうち、package.jsonを持たないものは個別照合の対象外です。')
    return rows, notes


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def fetch_json(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={
        'Content-Type': 'application/json', 'User-Agent': 'Errand-dependency-health'})
    opener = urllib.request.build_opener(NoRedirect(), HTTPSHandler())
    with opener.open(request, timeout=30) as response:
        body = response.read(4 * 1024 * 1024 + 1)
    if len(body) > 4 * 1024 * 1024:
        raise ValueError('advisory response too large')
    return json.loads(body)


def osv_findings(packages, fetch=fetch_json):
    queries = [{'package': {'ecosystem': p['ecosystem'], 'name': p['name']}, 'version': p['version']}
               for p in packages if p.get('ecosystem')]
    pending = list(enumerate(queries))
    results = [[] for _ in queries]
    seen = set()
    for _ in range(100):
        if not pending:
            return [(queries[i], sorted(set(ids))) for i, ids in enumerate(results) if ids]
        response = fetch('https://api.osv.dev/v1/querybatch', {'queries': [q for _, q in pending]})
        values = response.get('results')
        if not isinstance(values, list) or len(values) != len(pending):
            raise ValueError('invalid OSV response')
        next_page = []
        for (index, query), value in zip(pending, values):
            if not isinstance(value, dict) or set(value) - {'vulns', 'next_page_token'}:
                raise ValueError('invalid OSV result')
            vulnerabilities = value.get('vulns', [])
            if not isinstance(vulnerabilities, list):
                raise ValueError('invalid vulnerability list')
            for item in vulnerabilities:
                if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                        or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', item['id'])):
                    raise ValueError('invalid vulnerability ID')
                results[index].append(item['id'])
            token = value.get('next_page_token')
            if token is not None and token != '':
                if not isinstance(token, str) or (index, token) in seen:
                    raise ValueError('invalid/repeated OSV page token')
                seen.add((index, token))
                next_page.append((index, {**query, 'page_token': token}))
        pending = next_page
    raise ValueError('OSV pagination limit reached')


def build_license_findings(packages, fetch=fetch_json):
    findings = []
    for package in packages:
        if not package.get('scope', '').startswith('pinned build'):
            continue
        name, version = package['name'], package['version']
        info = fetch(f'https://pypi.org/pypi/{name}/{version}/json')['info']
        license = info.get('license_expression') or info.get('license')
        if not isinstance(license, str) or not license:
            raise ValueError('missing upstream license metadata')
        if license != package['license']:
            findings.append(f'{name}: PyPIのライセンス宣言が確認済みの値と異なります。')
    return findings


def node_findings(packages, fetch=fetch_json):
    nodes = [p for p in packages if p['name'] == 'Node.js']
    if not nodes:
        return []
    releases = fetch('https://nodejs.org/dist/index.json')
    if not isinstance(releases, list) or not releases:
        raise ValueError('invalid Node release list')
    findings = []
    for node in nodes:
        current = semver(node['version'])
        security = [semver(r['version']) for r in releases
                    if r.get('security') is True and semver(r['version'])[0] == current[0]]
        if not security:
            raise ValueError('Node security release baseline unavailable')
        if current < max(security):
            findings.append(f'Node.js {node["version"]}: 同じ系列のセキュリティ更新が未適用です。')
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', help='OSV/Node公式へ公開パッケージ名・版だけを照会')
    parser.add_argument('--json', action='store_true', help='ローカル保存・CI向けのJSON形式')
    args = parser.parse_args(argv)
    problems, unknown, notes, packages = [], [], [], []
    policy = json.loads(POLICY.read_text())
    packages.extend(build_packages(policy))
    for name, collect in (('Python', python_packages), ('Ubuntu native', ubuntu_packages)):
        try:
            packages.extend(collect())
        except Exception:
            unknown.append(f'{name}: 依存情報を収集できませんでした。この環境は別途確認が必要です。')
    try:
        cli, notes = cli_packages()
        packages.extend(cli)
    except Exception:
        unknown.append('CLI: 実際の実行ファイルと依存情報を特定できませんでした。')
    packages = list({(p['name'], p['version'], p.get('ecosystem')): p for p in packages}.values())
    try:
        problems, missing = evaluate(packages, policy)
        unknown.extend(missing)
    except Exception:
        unknown.append('依存ポリシーまたはバージョン情報を解釈できませんでした。')
    if args.online:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [(pool.submit(osv_findings, packages), 'OSV'), (pool.submit(node_findings, packages), 'Node.js'),
                    (pool.submit(build_license_findings, packages), 'PyPI licenses')]
            for future, name in jobs:
                try:
                    findings = future.result()
                    if name == 'OSV':
                        problems.extend(f'{q["package"]["name"]} {q["version"]}: {", ".join(ids)}'
                                        for q, ids in findings)
                    else:
                        problems.extend(findings)
                except Exception:
                    unknown.append(f'{name}: 脆弱性情報を取得・検証できませんでした。')
    else:
        unknown.append('オンラインの脆弱性照合は未実行です（--onlineで実行）。')
    result = {'checked_at': date.today().isoformat(), 'packages': packages, 'findings': problems,
              'unverified': unknown, 'coverage': notes}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for p in packages:
            print(f'{p["name"]} {p["version"]}: {p.get("license") or "license unknown"}')
        for title, values in (('要対応', problems), ('未確認', unknown), ('対象範囲', notes)):
            for value in values:
                print(f'{title}: {value}')
        print('判定:', '要対応' if problems else '未確認あり' if unknown else '照合範囲内で問題なし')
    return 1 if problems else 2 if unknown else 0


if __name__ == '__main__':
    raise SystemExit(main())
