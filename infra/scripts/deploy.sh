#!/usr/bin/env sh
# A release on the server (make deploy ENV=live TAG=vX.Y.Z; README, Hosting).
#
# The tag is the release: GitHub Actions built ghcr.io/.../public-jap-app and -ui for it
# (.github/workflows/release.yml), and this checks the same tag out so the compose files,
# the migrations and the image are one version. Rolling back is the same command with the
# tag before it.
#
# Nothing is built here. The server pulls, starts, waits for /api/health and migrates, which
# is what `make start ENV=live` does once a tag is checked out.
set -eu
cd "$(dirname "$0")/../.."

ENV_NAME="${1:-}"
TAG="${2:-}"

[ "$ENV_NAME" = live ] || {
  echo "make deploy is how the server releases: make deploy ENV=live TAG=vX.Y.Z" >&2
  exit 1
}
[ -n "$TAG" ] || { echo "Usage: make deploy ENV=live TAG=vX.Y.Z" >&2; exit 1; }
[ "$(uname)" != Darwin ] || {
  echo "live runs on the server, not this Mac (README, Hosting). Deploy with:" >&2
  echo "  ssh <server> 'cd /srv/public-jap && make deploy ENV=live TAG=$TAG'" >&2
  exit 1
}

# A dirty checkout means someone edited the server in place; checking out over it would
# throw that away without saying so.
[ -z "$(git status --porcelain)" ] || {
  echo "The checkout has local changes. Commit or discard them, then deploy." >&2
  git status --short >&2
  exit 1
}

git fetch --tags --prune
git rev-parse -q --verify "refs/tags/$TAG" > /dev/null || {
  echo "No tag $TAG. Tags here:" >&2
  git tag --list 'v*' --sort=-v:refname | head -5 >&2
  exit 1
}

was=$(git describe --tags --exact-match 2>/dev/null || git rev-parse --short HEAD)
git checkout --detach "refs/tags/$TAG"
echo "Deploying $TAG (was $was)"

# A fresh make: this one already worked out JAP_IMAGE_TAG from the old checkout and exported
# it, and `?=` would not look again.
exec env -u JAP_IMAGE_TAG make start ENV=live
