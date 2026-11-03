#!/usr/bin/env python3
"""Install from a signed APT archive in Docker and check tamper rejection."""

import argparse
from pathlib import Path
import subprocess

CHECK = r'''
install -d /etc/apt/keyrings
cp /repository/board-tune.asc /etc/apt/keyrings/board-tune.asc
printf 'Types: deb deb-src\nURIs: file:/repository\nSuites: stable\nComponents: main\nSigned-By: /etc/apt/keyrings/board-tune.asc\n' >/etc/apt/sources.list.d/board-tune.sources
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends board-tune dpkg-dev >/tmp/install.log 2>&1 || {
    tail -50 /tmp/install.log; exit 1;
}
sh /tests/smoke
cd /tmp
apt-get source board-tune
test -f board-tune-*/LICENSE
test -f board-tune-*/debian/rules
cp -a /repository /tmp/tampered
sed 's,file:/repository,file:/tmp/tampered,' /etc/apt/sources.list.d/board-tune.sources >/tmp/tampered.sources
check_rejected() {
    rm -rf /tmp/tampered-lists
    mkdir -p /tmp/tampered-lists/partial
    if LC_ALL=C apt-get -o Dir::Etc::sourcelist=/tmp/tampered.sources \
        -o Dir::Etc::sourceparts=- -o Dir::State::lists=/tmp/tampered-lists \
        -o APT::Update::Error-Mode=any update >/tmp/tampered.log 2>&1; then
        echo 'ERROR: APT accepted modified repository metadata'; exit 1
    fi
    grep -Eqi "$1" /tmp/tampered.log || { cat /tmp/tampered.log; exit 1; }
}
sed -i 's/Origin: board-tune/Origin: tampered/' /tmp/tampered/dists/stable/InRelease
check_rejected 'BADSIG|invalid signature|signature verification failed|Message has been manipulated'
cp /repository/dists/stable/InRelease /tmp/tampered/dists/stable/InRelease
python3 - <<'PY'
from pathlib import Path
import gzip
for path in Path('/tmp/tampered/dists/stable/main').glob('binary-*/Packages*'):
    if path.suffix == '.gz':
        path.write_bytes(gzip.compress(gzip.decompress(path.read_bytes()) + b'\n'))
    else:
        path.write_bytes(path.read_bytes() + b'\n')
PY
check_rejected 'Hash Sum mismatch|File has unexpected size'
echo 'APT install, matching source extraction, signature and index tamper rejection passed'
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--image', default='debian:bookworm-slim')
    parser.add_argument('--platform', help='optional Docker platform, such as linux/arm64')
    args = parser.parse_args()
    tests = Path(__file__).resolve().parents[1] / 'debian/tests'
    command = ['docker', 'run', '--rm']
    if args.platform:
        command += ['--platform', args.platform]
    command += ['--mount', f'type=bind,source={args.repo.resolve()},target=/repository,readonly',
                '--mount', f'type=bind,source={tests},target=/tests,readonly',
                args.image, 'sh', '-ec', CHECK]
    subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
