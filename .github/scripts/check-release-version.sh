set -euo pipefail

: "${VERSION:?release version is required}"
: "${GITHUB_SHA:?build commit is required}"

TAGS="$(git ls-remote --tags origin "refs/tags/$VERSION" "refs/tags/$VERSION^{}")"
COMMIT="$(printf '%s\n' "$TAGS" | awk '
  /\^\{\}$/ { peeled = $1; next }
  NF { direct = $1 }
  END { print peeled ? peeled : direct }
')"
if [ -n "$COMMIT" ] && [ "$COMMIT" != "$GITHUB_SHA" ]; then
  echo "release $VERSION belongs to $COMMIT, not build $GITHUB_SHA" >&2
  exit 1
fi
