#!/usr/bin/env bash
set -euo pipefail

: "${IMAGE:?image repository is required}"

VERSION="$(skopeo list-tags "docker://$IMAGE" | jq -r '.Tags[] | select(test("^v[0-9]+\\.[0-9]+\\.[0-9]+$"))' | sort -V | tail -n 1)"
if [ -z "$VERSION" ]; then
  echo "no published release version in $IMAGE" >&2
  exit 1
fi
DIGEST="sha256:$(skopeo inspect --raw "docker://$IMAGE:$VERSION" | sha256sum | cut -d ' ' -f 1)"
skopeo copy --all --preserve-digests "docker://$IMAGE@$DIGEST" "docker://$IMAGE:latest"
PUBLISHED="sha256:$(skopeo inspect --raw "docker://$IMAGE:latest" | sha256sum | cut -d ' ' -f 1)"
if [ "$PUBLISHED" != "$DIGEST" ]; then
  echo "$IMAGE:latest serves $PUBLISHED, expected $VERSION digest $DIGEST" >&2
  exit 1
fi
echo "$IMAGE:latest -> $VERSION ($DIGEST)"
