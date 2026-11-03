#!/usr/bin/env python3
"""Create a signed APT archive from board-tune binary and source packages."""

import argparse
import gzip
import hashlib
import html
from pathlib import Path
import shutil
import subprocess


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd)


def fields(text):
    return dict(line.split(': ', 1) for line in text.splitlines()
                if line and not line[0].isspace() and ': ' in line)


def build(packages, output, key, passphrase_file=None):
    candidates = sorted(p for p in packages.rglob('*') if p.is_file() and
                        p.name.endswith(('.deb', '.dsc', '.tar.xz', '.tar.gz')))
    binaries = [p for p in candidates if p.suffix == '.deb']
    if not binaries:
        raise ValueError('no .deb packages found')
    sources = {}
    for path in candidates:
        if path.suffix == '.dsc':
            metadata = fields(path.read_text())
            if metadata.get('Source') != 'board-tune':
                raise ValueError(f'{path}: unexpected source package')
            sources[metadata['Version']] = path
            # Require complete source assets before publishing any binaries.
            checksum_lines = path.read_text().split('Checksums-Sha256:\n', 1)[1].split('\n\n', 1)[0]
            for line in checksum_lines.splitlines():
                if not line.startswith(' '):
                    break
                digest, size, name = line.split()
                asset = path.parent / name
                if Path(name).name != name or not asset.is_file():
                    raise ValueError(f'{path}: missing source asset {name}')
                data = asset.read_bytes()
                if len(data) != int(size) or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f'{asset}: source checksum mismatch')
    for path in binaries:
        metadata = fields(run('dpkg-deb', '--field', str(path), 'Package', 'Version', 'Architecture').decode())
        if metadata.get('Package') != 'board-tune' or metadata.get('Architecture') != 'all':
            raise ValueError(f'{path}: expected board-tune Architecture: all')
        if metadata['Version'] not in sources:
            raise ValueError(f'{path}: matching .dsc and source tarballs are required')
    output.mkdir(parents=True, exist_ok=False)
    try:
        pool = output / 'pool/main/b/board-tune'
        pool.mkdir(parents=True)
        for path in candidates:
            destination = pool / path.name
            if destination.exists() and destination.read_bytes() != path.read_bytes():
                raise ValueError(f'{path.name}: conflicting assets with the same filename')
            shutil.copyfile(path, destination)
        suite = output / 'dists/stable'
        architectures = ('amd64', 'arm64', 'armhf')
        binary_index = run('apt-ftparchive', 'packages', 'pool', cwd=output)
        indexes = {suite / 'main/source/Sources': run('apt-ftparchive', 'sources', 'pool', cwd=output)}
        indexes.update({suite / f'main/binary-{arch}/Packages': binary_index for arch in architectures})
        for path, data in indexes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.with_name(path.name + '.gz').write_bytes(gzip.compress(data, mtime=0))
        command = ['apt-ftparchive']
        for name, value in {'Origin': 'board-tune', 'Label': 'board-tune',
                            'Suite': 'stable', 'Codename': 'stable', 'Components': 'main',
                            'Architectures': ' '.join(architectures)}.items():
            command.extend(['-o', f'APT::FTPArchive::Release::{name}={value}'])
        (suite / 'Release').write_bytes(run(*command, 'release', 'dists/stable', cwd=output))
        signing = ['gpg', '--batch', '--yes', '--local-user', key, '--digest-algo', 'SHA256']
        if passphrase_file:
            signing += ['--pinentry-mode', 'loopback', '--passphrase-file', str(passphrase_file)]
        subprocess.run([*signing, '--clearsign', '--output', str(suite / 'InRelease'),
                        str(suite / 'Release')], check=True)
        subprocess.run([*signing, '--armor', '--detach-sign', '--output', str(suite / 'Release.gpg'),
                        str(suite / 'Release')], check=True)
        public = run('gpg', '--batch', '--armor', '--export-options', 'export-minimal', '--export', key)
        if not public:
            raise ValueError('signing key could not be exported')
        (output / 'board-tune.asc').write_bytes(public)
        installer = Path(__file__).resolve().parents[1] / 'scripts/install-apt'
        shutil.copyfile(installer, output / 'install-apt')
        listing = run('gpg', '--batch', '--with-colons', '--list-keys', key).decode()
        fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
        (output / '.nojekyll').touch()
        (output / 'index.html').write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>board-tune APT repository</title><h1>board-tune APT repository</h1>'
            '<p>Debian 12/13 and Ubuntu packages; amd64, arm64 and armhf.</p>'
            '<h2>Install</h2><pre>curl -fsSLO https://trofymenko-r.github.io/board-tune/install-apt\n'
            'sudo sh ./install-apt</pre>'
            '<p><a href="install-apt">Download standalone installer</a> '
            '(no Git checkout or virtualenv required).</p>'
            '<p><a href="board-tune.asc">Archive signing key</a></p>'
            f'<p>Key fingerprint: <code>{html.escape(fingerprint)}</code></p>'
            '<p><a href="https://github.com/trofymenko-r/board-tune/blob/main/docs/distribution.md">'
            'Installation and source code</a></p></html>\n')
        for path in [output, *output.rglob('*')]:
            path.chmod(0o755 if path.is_dir() else 0o644)
        return fingerprint
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory; existing paths are refused')
    parser.add_argument('--key', required=True, help='GPG signing key fingerprint')
    parser.add_argument('--passphrase-file', type=Path)
    args = parser.parse_args()
    fingerprint = build(args.packages.resolve(), args.output.resolve(), args.key, args.passphrase_file)
    print('Archive signed by:', fingerprint)
    print('APT root:', args.output.resolve())


if __name__ == '__main__':
    main()
