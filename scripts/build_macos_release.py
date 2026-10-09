#!/usr/bin/env python3
"""Build a small native Mac trial ZIP and a separate corresponding-source ZIP."""

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
CODEX_VERSION = json.loads((ROOT / 'errand/runtime_manifest.json').read_text())['version']


def run(argv, **kwargs):
    return subprocess.run([str(arg) for arg in argv], check=True, **kwargs)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, destination, checksum=None):
    if urlsplit(url).scheme != 'https':
        raise ValueError('Only HTTPS source downloads are accepted')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and (not checksum or digest(destination) == checksum):
        return destination
    temporary = destination.with_suffix(destination.suffix + '.partial')
    try:
        request = Request(url, headers={'User-Agent': 'Errand-distribution-builder'})
        with urlopen(request, timeout=60) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        if checksum and digest(temporary) != checksum:
            raise ValueError(f'Source checksum mismatch: {destination.name}')
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def brew_components(analysis):
    components = {}
    for section in ast.literal_eval(analysis.read_text()):
        if not isinstance(section, list):
            continue
        for entry in section:
            if not isinstance(entry, tuple) or len(entry) != 3:
                continue
            path = Path(entry[1]).resolve()
            if 'Cellar' not in path.parts:
                continue
            index = path.parts.index('Cellar')
            name, version = path.parts[index + 1:index + 3]
            components[name] = Path(*path.parts[:index + 3])
    return components


def source_info(name, keg):
    recipe = keg / '.brew' / f'{name}.rb'
    text = recipe.read_text()
    url = re.search(r'^  url "([^"]+)"', text, re.M)
    checksum = re.search(r'^  sha256 "([0-9a-f]{64})"', text, re.M)
    if not url or not checksum or '#{' in url.group(1):
        raise ValueError(f'Cannot determine exact verified source for {name}')
    # Preserve the original recipe as well as its upstream source.
    return {'name': name, 'version': keg.name, 'source_url': url.group(1),
            'source_sha256': checksum.group(1)}, recipe


def build_cairo(output, cache):
    """Avoid Cairo's optional GPL LZO codec without changing its public API."""
    keg = Path(subprocess.check_output(['brew', '--prefix', 'cairo'], text=True).strip()).resolve()
    info, _ = source_info('cairo', keg)
    archive = download(info['source_url'], cache / Path(urlsplit(info['source_url']).path).name,
                       info['source_sha256'])
    sources = output / 'cairo-source'
    sources.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(sources, filter='data')
    source = next(path for path in sources.iterdir() if path.is_dir())
    build = output / 'cairo-build'
    env = os.environ.copy()
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    repository = Path(subprocess.check_output(['brew', '--repository'], text=True).strip())
    system_pkgconfig = repository / 'Library/Homebrew/os/mac/pkgconfig' / platform.mac_ver()[0].split('.')[0]
    if system_pkgconfig.is_dir():
        env['PKG_CONFIG_PATH'] = os.pathsep.join(filter(None,
            (env.get('PKG_CONFIG_PATH'), str(system_pkgconfig))))
    run([sys.executable, '-m', 'mesonbuild.mesonmain', 'setup', build, source,
         '--wrap-mode=nodownload', '-Dlzo=disabled', '-Dtests=disabled',
         '-Dgtk_doc=false', '-Dfontconfig=enabled', '-Dfreetype=enabled'], env=env)
    run([sys.executable, '-m', 'mesonbuild.mesonmain', 'compile', '-C', build], env=env)
    return build / 'util/cairo-script/libcairo-script-interpreter.2.dylib'


def collect_notices(components, destination, cache):
    sources = destination / 'Sources'
    licenses = destination / 'Licenses'
    sources.mkdir()
    licenses.mkdir()
    manifest = []

    def collect(item):
        name, keg = item
        info, recipe = source_info(name, keg)
        folder = licenses / name
        folder.mkdir()
        shutil.copy2(recipe, folder / recipe.name)
        for path in keg.iterdir():
            if path.is_file() and re.match(r'(?i)^(copying|license|notice|copyright|authors)', path.name):
                shutil.copy2(path, folder / path.name)
        filename = name.replace('@', '-') + '-' + Path(urlsplit(info['source_url']).path).name
        archive = download(info['source_url'], cache / filename, info['source_sha256'])
        shutil.copy2(archive, sources / filename)
        info['source_file'] = 'Sources/' + filename
        print('Collected source and notices:', name, flush=True)
        return info

    with ThreadPoolExecutor(max_workers=4) as executor:
        manifest.extend(executor.map(collect, sorted(components.items())))

    # Include PyInstaller's bootloader exception, Python hooks, and full source.
    version = importlib.metadata.version('pyinstaller')
    with urlopen(f'https://pypi.org/pypi/pyinstaller/{version}/json', timeout=30) as response:
        metadata = json.load(response)
    source = next(item for item in metadata['urls'] if item['packagetype'] == 'sdist')
    archive = download(source['url'], cache / source['filename'], source['digests']['sha256'])
    shutil.copy2(archive, sources / source['filename'])
    folder = licenses / 'PyInstaller'
    folder.mkdir()
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            if member.isfile() and Path(member.name).name in {'COPYING.txt', 'LICENSE', 'NOTICE'}:
                with stream.extractfile(member) as file:
                    (folder / Path(member.name).name).write_bytes(file.read())
    manifest.append({'name': 'PyInstaller', 'version': version, 'source_url': source['url'],
                     'source_sha256': source['digests']['sha256'], 'source_file': 'Sources/' + source['filename']})
    return manifest


def add_codex_notices(destination, cache):
    version = CODEX_VERSION
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('A stable Codex release is required')
    tag = 'rust-v' + version
    licenses = destination / 'Licenses/Codex'
    licenses.mkdir()
    for name in ('LICENSE', 'NOTICE'):
        url = f'https://raw.githubusercontent.com/openai/codex/{tag}/{name}'
        shutil.copy2(download(url, cache / f'codex-{version}-{name}'), licenses / name)
    filename = f'codex-{version}-source.tar.gz'
    url = f'https://codeload.github.com/openai/codex/tar.gz/refs/tags/{tag}'
    source = download(url, cache / filename)
    shutil.copy2(source, destination / 'Sources' / filename)
    return {'name': 'Codex', 'version': version, 'source_url': url, 'source_sha256': digest(source),
            'source_file': 'Sources/' + filename}


def build(output):
    if sys.platform != 'darwin':
        raise ValueError('Build on macOS with the matching native GTK libraries')
    arch = platform.machine()
    if arch not in {'arm64', 'x86_64'}:
        raise ValueError('Unsupported architecture')
    output.mkdir(parents=True, exist_ok=False)
    cache = ROOT / 'dist/source-cache'
    codec = build_cairo(output, cache)
    env = os.environ.copy()
    env.update(ERRAND_BUILD_ARCH=arch,
               ERRAND_BUILD_CAIRO_SCRIPT=str(codec.resolve()))
    prefix = Path(subprocess.check_output(['brew', '--prefix'], text=True).strip())
    env['DYLD_LIBRARY_PATH'] = str(prefix / 'lib')
    run([sys.executable, '-m', 'PyInstaller', '--distpath', output / 'build',
         '--workpath', output / 'work', ROOT / 'scripts/Errand.spec'], env=env)
    label = 'AppleSilicon' if arch == 'arm64' else 'Intel'
    release = output / f'Errand-Trial-{label}'
    release.mkdir()
    app = release / 'Errand.app'
    shutil.copytree(output / 'build/Errand.app', app, symlinks=True)
    components = brew_components(output / 'work/Errand/Analysis-00.toc')
    # Custom Cairo has the same upstream source with an explicit build option.
    components['cairo'] = Path(subprocess.check_output(['brew', '--prefix', 'cairo'], text=True).strip()).resolve()
    components.pop('lzo', None)
    manifest = collect_notices(components, release, cache)
    manifest.append(add_codex_notices(release, cache))
    for name in ('START_HERE.html', 'LICENSES_README.txt'):
        shutil.copy2(ROOT / 'distribution' / name, release / name)
    cpu = 'Apple Silicon' if arch == 'arm64' else 'Intel'
    other = 'Intel' if arch == 'arm64' else 'Apple Silicon'
    guide = release / 'START_HERE.html'
    guide.write_text(guide.read_text().replace('<h1>ErrandをMacで試す</h1>',
        f'<h1>ErrandをMacで試す</h1>\n<p><strong>このZIPは{cpu}向けです。'
        f'{other}のMacでは使用できません。</strong></p>'))
    source = release / 'Sources/errand'
    source.mkdir()
    for name in ('app.py', 'LICENSE', 'README.md', 'CONTRIBUTING.md',
                 'extension.js', 'prefs.js', 'shortcutPreferences.js', 'metadata.json'):
        shutil.copy2(ROOT / name, source / name)
    for name in ('errand', 'scripts', 'distribution', 'tests', '.github', 'schemas'):
        shutil.copytree(ROOT / name, source / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'gschemas.compiled'))
    sources_release = output / f'Errand-Sources-{label}'
    sources_release.mkdir()
    shutil.move(release / 'Sources', sources_release / 'Sources')
    sources_archive = output / (sources_release.name + '.zip')
    metadata = {'architecture': arch, 'minimum_macos': '15.0', 'version': '0.2.0',
                'signing': 'ad-hoc; not notarized', 'components': manifest,
                'codex_installation': 'download on user request; reuse existing CLI when available',
                'sources_archive': sources_archive.name,
                'cairo_script_build_options': ['-Dlzo=disabled']}
    (release / 'BUILD_INFO.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    for name in ('BUILD_INFO.json', 'LICENSES_README.txt'):
        shutil.copy2(release / name, sources_release / name)
    shutil.copytree(release / 'Licenses', sources_release / 'Licenses')
    with (release / 'LICENSES_README.txt').open('a') as stream:
        stream.write(f'\n対応ソースは、同じ配布投稿にある{sources_archive.name}に収録しています。\n')
    resources = app / 'Contents/Resources'
    shutil.copytree(release / 'Licenses', resources / 'Licenses')
    shutil.copy2(release / 'LICENSES_README.txt', resources / 'LICENSES_README.txt')
    run(['codesign', '--force', '--sign', '-', app])
    run(['codesign', '--verify', '--deep', '--strict', app])
    run([app / 'Contents/MacOS/Errand', '--smoke-test'])
    run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', sources_release, sources_archive])
    sources_archive.with_suffix('.zip.sha256').write_text(digest(sources_archive) + '  ' + sources_archive.name + '\n')
    archive = output / (release.name + '.zip')
    run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', release, archive])
    archive.with_suffix('.zip.sha256').write_text(digest(archive) + '  ' + archive.name + '\n')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/distribution' / platform.machine())
    args = parser.parse_args()
    print(build(args.output.resolve()))
