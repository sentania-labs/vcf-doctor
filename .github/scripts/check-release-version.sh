#!/usr/bin/env bash
set -euo pipefail

: "${VERSION:?release version is required}"
: "${GITHUB_SHA:?build commit is required}"
: "${MAINLINE_REF:=origin/main}"

if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "release tag '$VERSION' must match vMAJOR.MINOR.PATCH" >&2
  exit 2
fi

if ! TAGS="$(git ls-remote --tags origin "refs/tags/$VERSION" "refs/tags/$VERSION^{}")"; then
  echo "could not read release tag $VERSION from origin" >&2
  exit 2
fi
COMMIT="$(printf '%s\n' "$TAGS" | awk '
  /\^\{\}$/ { peeled = $1; next }
  NF { direct = $1 }
  END { print peeled ? peeled : direct }
')"
if [ -z "$COMMIT" ]; then
  echo "release tag $VERSION does not exist on origin" >&2
  exit 2
fi
if [ "$COMMIT" != "$GITHUB_SHA" ]; then
  echo "release $VERSION belongs to $COMMIT, not build $GITHUB_SHA" >&2
  exit 1
fi

LOCAL_COMMIT="$(git rev-parse --verify --quiet "refs/tags/$VERSION^{commit}")" || {
  echo "release tag $VERSION does not exist in this checkout" >&2
  exit 2
}
if [ "$LOCAL_COMMIT" != "$GITHUB_SHA" ]; then
  echo "checked-out tag $VERSION belongs to $LOCAL_COMMIT, not build $GITHUB_SHA" >&2
  exit 1
fi

if ! git rev-parse --verify --quiet "$MAINLINE_REF^{commit}" >/dev/null; then
  echo "mainline ref '$MAINLINE_REF' does not exist in this checkout" >&2
  exit 2
fi
if ! git merge-base --is-ancestor "$GITHUB_SHA" "$MAINLINE_REF"; then
  echo "release tag $VERSION points at $GITHUB_SHA, which is not reachable from $MAINLINE_REF" >&2
  echo "merge the change to main first, then tag the merged commit" >&2
  exit 1
fi

echo "release tag $VERSION is well formed, belongs to $GITHUB_SHA, and is on $MAINLINE_REF"
