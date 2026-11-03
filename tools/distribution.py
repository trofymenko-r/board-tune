#!/usr/bin/env python3
"""Small maintainer commands for local packages and an explicit future release."""

import argparse
from datetime import datetime
from email.utils import format_datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'board-tune-packaging:bookworm'
REPOSITORY = 'trofymenko-r/board-tune'
URL = 'https://trofymenko-r.github.io/board-tune/'


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), cwd=ROOT, check=True, **kwargs)


def capture(argv):
    return subprocess.check_output(list(map(str, argv)), cwd=ROOT, text=True).strip()


def logged(argv, name):
    log = ROOT / 'dist/logs' / (name + '.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('wb') as stream:
        try:
            run(argv, stdout=stream, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError:
            print('\n'.join(log.read_text(errors='replace').splitlines()[-25:]), file=sys.stderr)
            raise SystemExit(f'Failed; full log: {log}') from None


def docker(*args):
    return ['docker', 'run', '--rm', '--platform', 'linux/' + capture(
        ['docker', 'version', '--format', '{{.Server.Arch}}']),
        '--user', f'{os.getuid()}:{os.getgid()}', *args]


def image():
    if os.getuid() == 0:
        raise SystemExit('Run local package builds as a regular user with Docker access')
    print('Preparing the Debian build environment...', flush=True)
    logged(['docker', 'build', '--build-arg', f'BUILDER_UID={os.getuid()}',
            '-t', IMAGE, '-f', ROOT / 'packaging/Containerfile', ROOT / 'packaging'], 'build-image')


def build(args):
    image()
    base = ROOT / 'dist/builds'
    base.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='debian-', dir=base))
    print(f'Building {args.ref} and running tests...', flush=True)
    logged(docker('-v', f'{ROOT}:/src:ro', '-v', f'{output}:/out', IMAGE,
                  'python3', '/src/tools/build_deb.py', '--ref', args.ref, '--output', '/out'), 'build-deb')
    logged(docker('-v', f'{output}:/packages:ro', IMAGE, 'sh', '-c',
                  'lintian /packages/*.changes'), 'lintian')
    if not list(output.glob('*.deb')) or not list(output.glob('*.dsc')):
        raise SystemExit(f'The build did not produce binary and source packages: {output}')
    latest = ROOT / 'dist/latest-build'
    if latest.exists() and not latest.is_symlink():
        raise SystemExit(f'{latest}: expected a symlink; keeping the completed build at {output}')
    replacement = latest.with_name('latest-build.new')
    replacement.symlink_to(output.relative_to(latest.parent))
    replacement.replace(latest)
    print(f'Packages: {latest}')
    print(f'Install: sudo apt install {next(latest.glob("*.deb"))}')


def signing_dir():
    directory = Path(capture(['git', 'rev-parse', '--absolute-git-dir'])) / 'apt-key'
    required = ('gnupg', 'passphrase', 'archive-private.asc', 'archive-public.asc', 'fingerprint')
    if not all((directory / name).exists() for name in required):
        raise SystemExit(f'Restore the private archive signing directory at {directory}; '
                         'the existing public archive key must not be silently replaced')
    if (directory / 'archive-public.asc').read_bytes() != (ROOT / 'packaging/board-tune-archive-key.asc').read_bytes():
        raise SystemExit('The private signing directory does not match the committed archive key')
    return directory


def prepare(args):
    packages = args.packages.resolve()
    if not packages.exists():
        raise SystemExit('Build packages first: scripts/build-deb')
    key = signing_dir()
    image()
    base = ROOT / 'dist/builds'
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='apt-', dir=base))
    print('Signing the local APT archive...', flush=True)
    logged(docker('-e', 'GNUPGHOME=/keys/gnupg', '-v', f'{ROOT}:/src:ro',
                  '-v', f'{packages}:/packages:ro', '-v', f'{key}:/keys', '-v', f'{work}:/out',
                  IMAGE, 'python3', '/src/tools/build_apt_repo.py', '--packages', '/packages',
                  '--output', '/out/apt', '--key', (key / 'fingerprint').read_text().strip(),
                  '--passphrase-file', '/keys/passphrase'), 'prepare-apt')
    latest = ROOT / 'dist/latest-apt'
    if latest.exists() and not latest.is_symlink():
        raise SystemExit(f'{latest}: expected a symlink; prepared archive is at {work / "apt"}')
    replacement = latest.with_name('latest-apt.new')
    replacement.symlink_to((work / 'apt').relative_to(latest.parent))
    replacement.replace(latest)
    print(f'Signed archive: {latest}')


def check(args):
    if not args.repo.exists():
        raise SystemExit('Prepare a local archive first: scripts/prepare-apt')
    print('Checking APT install, source retrieval and tamper rejection...', flush=True)
    command = [sys.executable, ROOT / 'tools/check_apt_repo.py', '--repo', args.repo.resolve(),
               '--image', args.image, '--platform', args.platform or 'linux/' + capture(
                   ['docker', 'version', '--format', '{{.Server.Arch}}'])]
    logged(command, 'check-apt')
    print('APT installation, sources and signature/index integrity checks passed')


def bump(args):
    version = args.version
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise SystemExit('Use a version such as 0.1.1')
    changelog = ROOT / 'debian/changelog'
    old = re.match(r'board-tune \(([^)]+)\)', changelog.read_text())[1].split('-')[0]
    if tuple(map(int, version.split('.'))) <= tuple(map(int, old.split('.'))):
        raise SystemExit(f'The version must be newer than {old}')
    updates = {}
    for name, pattern, replacement in [
        ('pyproject.toml', r'(?m)^version = "[^"]+"$', f'version = "{version}"'),
        ('board_tune/__init__.py', r'(?m)^__version__ = "[^"]+"$', f'__version__ = "{version}"'),
        ('debian/board-tune.1', r'"board-tune [^"]+"', f'"board-tune {version}"'),
    ]:
        path = ROOT / name
        updates[path], count = re.subn(pattern, replacement, path.read_text())
        if count != 1:
            raise SystemExit(f'{name}: expected one version field; no files changed')
    maintainer = capture(['git', 'config', 'user.name']) + ' <' + capture(['git', 'config', 'user.email']) + '>'
    updates[changelog] = (f'board-tune ({version}-1) unstable; urgency=medium\n\n'
                          f'  * New upstream release.\n\n -- {maintainer}  '
                          f'{format_datetime(datetime.now().astimezone())}\n\n' + changelog.read_text())
    for path, text in updates.items():
        path.write_text(text)
    print(f'Updated version to {version}; review and commit the changes before publishing')


def github():
    executable = shutil.which('gh')
    if executable:
        return executable
    local = Path(capture(['git', 'rev-parse', '--absolute-git-dir'])) / 'github-cli'
    if (local / 'gh').exists():
        if (local / 'config/hosts.yml').exists():
            os.environ.setdefault('GH_CONFIG_DIR', str(local / 'config'))
        return str(local / 'gh')
    raise SystemExit('Install GitHub CLI and run: gh auth login --web --scopes workflow')


def publish(args):
    # A dry run is local only: no login, secrets, repository creation or pushes.
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.tag):
        raise SystemExit('Use a release tag such as v0.1.1')
    if args.dry_run:
        print(f'Would publish {args.tag} to {REPOSITORY}:')
        print('  validate clean Git tree, version, tag and archive key')
        print('  verify GitHub login; create the public repository if missing')
        print('  configure Pages and signing secrets; push main and release tag')
        print('  wait for the release workflow and report its result')
        print('Dry run: no changes made and nothing published')
        return
    if capture(['git', 'status', '--porcelain']):
        raise SystemExit('Commit or discard local changes before publishing')
    version = re.match(r'board-tune \(([^)]+)\)', (ROOT / 'debian/changelog').read_text())[1]
    upstream = version.rsplit('-', 1)[0]
    python_version = re.search(r'(?m)^__version__ = "([^"]+)"',
                               (ROOT / 'board_tune/__init__.py').read_text())[1]
    project_version = re.search(r'(?m)^version = "([^"]+)"', (ROOT / 'pyproject.toml').read_text())[1]
    if python_version != upstream or project_version != upstream:
        raise SystemExit('Version files differ; use scripts/bump-version first')
    if args.tag != 'v' + version.rsplit('-', 1)[0]:
        raise SystemExit('Tag and package version differ; use scripts/bump-version first')
    tag_exists = subprocess.run(
        ['git', 'rev-parse', '--verify', '--quiet', f'refs/tags/{args.tag}'], cwd=ROOT,
        capture_output=True).returncode == 0
    if tag_exists and capture(['git', 'rev-parse', args.tag + '^{}']) != capture(['git', 'rev-parse', 'HEAD']):
        raise SystemExit('The tag points to another commit; choose a new version instead of moving it')
    key = signing_dir()
    gh = github()
    try:
        login = capture([gh, 'api', 'user', '--jq', '.login'])
    except subprocess.CalledProcessError:
        raise SystemExit(f'Authorize GitHub first: {shlex.quote(gh)} auth login --web --scopes workflow') from None
    if login.lower() != REPOSITORY.split('/')[0].lower():
        raise SystemExit(f'Authenticate GitHub CLI as {REPOSITORY.split("/")[0]}')
    info = subprocess.run([gh, 'api', f'repos/{REPOSITORY}'], cwd=ROOT, capture_output=True, text=True)
    if info.returncode:
        if '404' not in info.stderr:
            raise SystemExit(info.stderr.strip())
        run([gh, 'repo', 'create', REPOSITORY, '--public', '--description',
             'Configure Linux boards over SSH or through offline boot media'])
    elif json.loads(info.stdout)['private']:
        raise SystemExit('The existing repository is private; refusing to change its visibility')
    # Let gh supply credentials through a transient helper, without editing Git configuration.
    git = ['git', '-c', 'credential.helper=', '-c',
           'credential.helper=!' + shlex.quote(gh) + ' auth git-credential']
    destination = f'https://github.com/{REPOSITORY}.git'
    remote_tag = capture([*git, 'ls-remote', '--tags', destination, f'refs/tags/{args.tag}'])
    if remote_tag and (not tag_exists or remote_tag.split()[0] != capture(['git', 'rev-parse', args.tag])):
        raise SystemExit('A different remote release tag already exists; it was not overwritten')
    run([*git, 'push', destination, 'HEAD:refs/heads/main'])
    for name, filename in [('APT_SIGNING_KEY', 'archive-private.asc'),
                           ('APT_SIGNING_PASSPHRASE', 'passphrase')]:
        run([gh, 'secret', 'set', name, '--repo', REPOSITORY], input=(key / filename).read_bytes())
    pages = subprocess.run([gh, 'api', f'repos/{REPOSITORY}/pages'], cwd=ROOT, capture_output=True, text=True)
    if pages.returncode and '404' not in pages.stderr:
        raise SystemExit(pages.stderr.strip())
    run([gh, 'api', f'repos/{REPOSITORY}/pages', '--method',
         'PUT' if pages.returncode == 0 else 'POST', '-f', 'build_type=workflow'], stdout=subprocess.DEVNULL)
    previous = json.loads(capture([gh, 'run', 'list', '--repo', REPOSITORY,
                                  '--workflow', 'release.yml', '--branch', args.tag,
                                  '--json', 'databaseId']))
    previous = {item['databaseId'] for item in previous}
    if not tag_exists:
        run(['git', 'tag', '-a', args.tag, '-m', f'board-tune {args.tag[1:]}'])
    if remote_tag:
        run([gh, 'workflow', 'run', 'release.yml', '--repo', REPOSITORY, '--ref', args.tag])
    else:
        run([*git, 'push', destination, f'refs/tags/{args.tag}'])
    for _ in range(30):
        runs = json.loads(capture([gh, 'run', 'list', '--repo', REPOSITORY,
                                  '--workflow', 'release.yml', '--branch', args.tag,
                                  '--json', 'databaseId']))
        fresh = next((item['databaseId'] for item in runs if item['databaseId'] not in previous), None)
        if fresh is not None:
            run([gh, 'run', 'watch', str(fresh), '--repo', REPOSITORY, '--exit-status'])
            print(f'Release workflow completed: {URL}')
            return
        time.sleep(2)
    raise SystemExit('The workflow has not appeared yet; inspect GitHub Actions before retrying')


def install(args):
    run(['sh', ROOT / 'scripts/install-apt', *(['--dry-run'] if args.dry_run else [])])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build_parser = commands.add_parser('build', prog='build-deb', help='build and test a Debian package in Docker')
    build_parser.add_argument('--ref', default='HEAD')
    build_parser.set_defaults(action=build)
    apt = commands.add_parser('apt', prog='prepare-apt', help='prepare a signed local APT archive')
    apt.add_argument('--packages', type=Path, default=ROOT / 'dist/latest-build')
    apt.set_defaults(action=prepare)
    verify = commands.add_parser('check', prog='check-apt', help='test APT installation and integrity')
    verify.add_argument('--repo', type=Path, default=ROOT / 'dist/latest-apt')
    verify.add_argument('--image', default='debian:bookworm-slim')
    verify.add_argument('--platform')
    verify.set_defaults(action=check)
    version = commands.add_parser('version', prog='bump-version', help='update version files for a future release')
    version.add_argument('version')
    version.set_defaults(action=bump)
    release = commands.add_parser('publish', prog='publish-release', help='explicitly publish a GitHub Pages release')
    release.add_argument('tag')
    release.add_argument('--dry-run', action='store_true')
    release.set_defaults(action=publish)
    client = commands.add_parser('install', prog='install-apt', help='add the public APT source and install board-tune')
    client.add_argument('--dry-run', action='store_true')
    client.set_defaults(action=install)
    args = parser.parse_args(argv)
    try:
        args.action(args)
    except FileNotFoundError as error:
        parser.exit(1, f'Required tool or file is missing: {error.filename}\n')
    except subprocess.CalledProcessError as error:
        parser.exit(1, f'Command failed (exit {error.returncode})\n')


if __name__ == '__main__':
    main()
