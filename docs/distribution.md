# Debian packages and APT distribution

board-tune is licensed under **GPL-3.0-only**. See [LICENSE](../LICENSE).
Public publication is deferred at the owner's request (2026-11-03). Packages
and the signed archive have been prepared and tested locally. Do not push
release tags or run the deployment workflow until publication is requested.
The repository URL becomes available after the first successful deployment.

## Install through APT

The planned archive URL is `https://trofymenko-r.github.io/board-tune/`;
it has not been published yet. The commands below are for the future release.
Its `stable` suite tracks board-tune releases, not a particular Debian release.
The initial signing fingerprint is
`B8E559579253CC9310CDB77279B88B4171E11938`; the public key is also tracked in
`packaging/board-tune-archive-key.asc`. The workflow requires its signing secret
to match this key. Private signing material is not tracked.
The package is architecture-independent; indexes cover amd64, arm64 and armhf.
The baseline is Debian 12 / Raspberry Pi OS Bookworm and newer, and Ubuntu 24.04
and newer. Runtime dependencies come from the operating system repositories.

Download the archive key and compare its fingerprint with the fingerprint
published on the archive's index page and in the GitHub release notes:

```sh
curl -fsSLo board-tune.asc https://trofymenko-r.github.io/board-tune/board-tune.asc
gpg --show-keys --with-fingerprint board-tune.asc
sudo install -d -m 755 /etc/apt/keyrings
sudo install -m 644 board-tune.asc /etc/apt/keyrings/board-tune.asc
sudo tee /etc/apt/sources.list.d/board-tune.sources >/dev/null <<'EOF'
Types: deb deb-src
URIs: https://trofymenko-r.github.io/board-tune/
Suites: stable
Components: main
Architectures: amd64 arm64 armhf
Signed-By: /etc/apt/keyrings/board-tune.asc
EOF
sudo apt update
sudo apt install board-tune
```

Do not use `apt-key`, `trusted=yes` or disabled signature checking. The key is
scoped to this repository. Fetch matching source with `apt source board-tune`;
every published binary version includes its `.dsc` and source tarballs.

A downloaded package can also be installed with
`sudo apt install ./board-tune_0.1.0-1_all.deb`.

## Build locally

The Debian package uses debhelper, dh-python and pybuild. It includes all Kconfig
resources, the command, documentation, a manual page and Python bytecode cleanup
provided by dh-python. No pip install or network access occurs in package
installation scripts. Installation does not modify any board configuration.

Build from a committed Git ref (the default is HEAD):

```sh
docker build --build-arg BUILDER_UID="$(id -u)" \
  -t board-tune-packaging -f packaging/Containerfile packaging
mkdir -p dist/debian
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD:/src:ro" -v "$PWD/dist/debian:/out" \
  board-tune-packaging python3 /src/tools/build_deb.py --output /out
```

Or install `debian/control` Build-Depends on a Debian host and run
`python3 tools/build_deb.py`. Builds run the full Python test suite as an
unprivileged user. The loop-device test requires a separate root-capable host.
Build helpers require Python 3.11 or newer (provided by the Bookworm container).
The output directory contains the `.deb`, corresponding source package,
`.changes` and `.buildinfo`. Published versions are immutable: change the version
before releasing different contents under an existing filename.

For an unpublished staged change, `--ref "$(git write-tree)"` builds the staged
tree. This excludes untracked files, `.git`, local credentials and virtualenvs.

## Sign a local APT archive

Create a dedicated archive signing key in a private GnuPG home. For example:

```sh
export GNUPGHOME="$HOME/.local/share/board-tune-archive-gnupg"
install -d -m 700 "$GNUPGHOME"
gpg --quick-generate-key \
  'board-tune APT archive <ruslan.trofymenko@oddsystems.io>' ed25519 sign 2y
gpg --list-secret-keys --with-fingerprint
python3 tools/build_apt_repo.py --packages dist/debian --output dist/apt \
  --key FULL_SIGNING_FINGERPRINT
```

The archive builder requires `apt-utils`, `dpkg-dev` and `gnupg`. It writes signed
`InRelease` and `Release.gpg`, binary indexes, source indexes, the public key and
an index page with its fingerprint. Pass `--passphrase-file PRIVATE_FILE` for
unattended signing; keep that file private and outside Git. An existing output
directory is refused rather than overwritten. Preserve older release assets
when building a replacement archive so existing versions remain downloadable.
Back up the signing key and renew it before its expiry.
When intentionally replacing the archive key, export the new public key into
`packaging/board-tune-archive-key.asc` and update the documented fingerprint
before deploying with new signing secrets. Existing clients need the new key.

## Publish with GitHub Pages

1. Create the public `trofymenko-r/board-tune` repository and push the source
   history. In Settings -> Pages, set the source to **GitHub Actions**.
2. Add the dedicated signing key as repository secret `APT_SIGNING_KEY` and its
   passphrase as `APT_SIGNING_PASSPHRASE`. Export the secret key into a private
   local file, then feed it to `gh secret set`; never paste it into chat or Git:

   ```sh
   umask 077
   gpg --armor --export-secret-keys FULL_SIGNING_FINGERPRINT >archive-private.asc
   gh secret set APT_SIGNING_KEY --repo trofymenko-r/board-tune <archive-private.asc
   gh secret set APT_SIGNING_PASSPHRASE --repo trofymenko-r/board-tune
   rm archive-private.asc
   ```

3. Keep `pyproject.toml`, `board_tune/__init__.py` and the upstream part of
   `debian/changelog` in sync. Commit changes using `scope: Capitalized summary`
   and `Signed-off-by: Ruslan Trofymenko <ruslan.trofymenko@oddsystems.io>`.
4. Push a matching version tag, such as `v0.1.0`. The workflow builds and tests
   packages, checks them with lintian, uploads binary and source release assets,
   collects earlier releases, signs the APT archive and deploys it to Pages.
   A manual workflow run must select a matching version tag, not main.
5. Publish the signing fingerprint in the release notes. Verify the live
   `InRelease` signature and perform an APT install from the Pages URL before
   announcing installation instructions.

No private key or passphrase is part of the Pages artifact. Changing the public
key requires a deliberate client key update; keep the same archive key across
ordinary releases.

Before deployment, the workflow installs from the signed archive in a clean
Bookworm container, fetches the matching source and checks that APT rejects both
a modified InRelease signature and modified package indexes. Run this locally
with `python3 tools/check_apt_repo.py --repo dist/apt`; `--image` and `--platform`
can be used for additional distribution and ARM checks.
