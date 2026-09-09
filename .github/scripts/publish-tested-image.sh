#!/usr/bin/env bash
set -euo pipefail

: "${IMAGE:?image repository is required}"
: "${VERSION:?build version is required}"
: "${TESTED:?tested digest is required}"
: "${GITHUB_OUTPUT:?step output file is required}"

if [[ "$GITHUB_REF" == refs/tags/* ]]; then
  : "${GITHUB_REPOSITORY:?GitHub repository is required}"
  RELEASE="$(gh api --paginate "repos/$GITHUB_REPOSITORY/releases?per_page=100" | jq -sc --arg version "$VERSION" '
    [ .[][] | select(.tag_name == $version) ]
  ')"
  if [ "$RELEASE" != '[]' ]; then
    DIGEST="$(jq -er '
      if length != 1 then error("expected one release record") else .[0] end
      | if .draft or .prerelease then error("release must be completed") else . end
      | (.body // "") | capture("Digest: (?<digest>sha256:[a-f0-9]{64})(?:[^a-f0-9]|$)").digest
    ' <<< "$RELEASE")"
    PUBLISHED="sha256:$(skopeo inspect --raw "docker://$IMAGE:$VERSION" | sha256sum | cut -d ' ' -f 1)"
    if [ "$PUBLISHED" != "$DIGEST" ]; then
      echo "$IMAGE:$VERSION serves $PUBLISHED, completed release records $DIGEST" >&2
      exit 1
    fi
    echo "Reusing completed release $VERSION ($DIGEST)"
    echo "digest=$DIGEST" >> "$GITHUB_OUTPUT"
    echo "reused=true" >> "$GITHUB_OUTPUT"
    exit 0
  fi
  TAG="$VERSION"
else
  TAG="sha-${GITHUB_SHA::7}"
fi
skopeo copy --all --preserve-digests "oci-archive:$RUNNER_TEMP/image.tar" "docker://$IMAGE:$TAG"
echo "digest=$TESTED" >> "$GITHUB_OUTPUT"
echo "reused=false" >> "$GITHUB_OUTPUT"
