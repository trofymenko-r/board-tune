#!/usr/bin/env python3
"""Build Debian binary and corresponding source packages from a Git tree."""

import argparse
import ast
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='committed Git ref or staged tree ID')
    parser.add_argument('--output', type=Path, default=Path('dist/debian'))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    archive = subprocess.check_output(['git', 'archive', '--format=tar', args.ref], cwd=repo)
    with tempfile.TemporaryDirectory(prefix='board-tune-deb-') as temporary:
        work = Path(temporary)
        source = work / 'source'
        source.mkdir()
        subprocess.run(['tar', '-xf', '-', '-C', str(source)], input=archive, check=True)
        version = subprocess.check_output(
            ['dpkg-parsechangelog', '-l', str(source / 'debian/changelog'), '-SVersion'],
            text=True).strip()
        upstream = version.rsplit('-', 1)[0]
        metadata = tomllib.loads((source / 'pyproject.toml').read_text())
        module = ast.parse((source / 'board_tune/__init__.py').read_text())
        module_version = next(ast.literal_eval(node.value) for node in module.body
                              if isinstance(node, ast.Assign) and
                              any(isinstance(name, ast.Name) and name.id == '__version__'
                                  for name in node.targets))
        if upstream != metadata['project']['version'] or upstream != module_version:
            parser.error('debian/changelog, pyproject.toml and __version__ must match')
        renamed = work / f'board-tune-{upstream}'
        source.rename(renamed)
        source = renamed
        if not (source / 'LICENSE').is_file():
            parser.error('the source tree must contain LICENSE before distribution')
        # Keep Debian packaging outside the upstream tarball for 3.0 (quilt).
        orig = work / f'board-tune_{upstream}.orig.tar.xz'
        with tarfile.open(fileobj=io.BytesIO(archive)) as original, tarfile.open(orig, 'w:xz') as target:
            for entry in original:
                if entry.name == 'debian' or entry.name.startswith('debian/'):
                    continue
                stream = original.extractfile(entry) if entry.isfile() else None
                entry.name = f'board-tune-{upstream}/{entry.name}'
                target.addfile(entry, stream)
        subprocess.run(['dpkg-buildpackage', '-us', '-uc'], cwd=source, check=True)
        output.mkdir(parents=True, exist_ok=True)
        for artifact in work.iterdir():
            if artifact.is_file() and artifact.name.startswith('board-tune_'):
                destination = output / artifact.name
                if destination.exists() and destination.read_bytes() != artifact.read_bytes():
                    raise SystemExit(f'{destination}: version already exists with different contents; bump version')
                shutil.copy2(artifact, destination)
                print(destination)


if __name__ == '__main__':
    main()
