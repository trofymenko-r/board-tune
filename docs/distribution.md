# Packages and releases

**Publication is deferred.** Packages and the APT archive are local. Run commands
from the project root. Local builds need Python 3 and Docker; Debian tools run
inside the container.

## Build and check locally

```sh
scripts/build-deb
scripts/prepare-apt
scripts/check-apt
```

`build-deb` prepares Docker, runs tests, builds binary/source packages and checks
them with lintian. Results are in `dist/latest-build/`; earlier builds are kept.
Detailed output is in `dist/logs/`. The default source is committed HEAD; use
`scripts/build-deb --ref REF` for another commit or staged tree ID. Untracked
files, credentials and virtualenvs are excluded.

`prepare-apt` signs packages into `dist/latest-apt/`, using the private maintainer
key directory `.git/apt-key/`. Back up this directory separately and restore it
on another machine; the script never replaces the archive key automatically.
`check-apt` verifies installation, source retrieval and rejection of modified
signatures/indexes in a clean container.

Install locally with `sudo apt install ./dist/latest-build/*.deb`.
Each script has `--help` for optional paths and distributions.

## Prepare a future version

```sh
scripts/bump-version 0.1.1
```

This updates Python metadata, the manual and Debian changelog together. Review
and commit with your Signed-off-by before publishing. The local `v0.1.0` tag
identifies an earlier preparation commit; scripts never move release tags.

## Publish later, when explicitly requested

```sh
scripts/publish-release v0.1.1 --dry-run
# Only when ready:
scripts/publish-release v0.1.1
```

The actual command requires GitHub CLI authenticated as `trofymenko-r` with
workflow access. It validates the clean tree, version, tag and archive key,
creates a public repository if missing, configures signing secrets and Pages,
pushes the release and waits for its workflow. Private keys go to GitHub Secrets
through stdin. Dry-run does none of this. Existing private repositories and
conflicting tags are refused; no force push is used.

The planned APT URL is `https://trofymenko-r.github.io/board-tune/`. Its stable
suite serves architecture-independent packages with amd64, arm64 and armhf
indexes. GPL-3.0-only applies; every binary version has matching source packages.
Runtime libraries come from the OS repositories.

The public key is `packaging/board-tune-archive-key.asc`, fingerprint
`B8E559579253CC9310CDB77279B88B4171E11938`. Keep it stable across ordinary releases.

## Install from APT after publication

```sh
scripts/install-apt --dry-run
sudo scripts/install-apt
```

This adds the committed key with a repository-specific Signed-By entry and
installs board-tune. Different existing key/source files are not overwritten.
Once configured, use normal `sudo apt update` and `sudo apt upgrade`.
