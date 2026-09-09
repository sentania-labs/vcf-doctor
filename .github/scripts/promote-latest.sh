#!/usr/bin/env bash
set -euo pipefail

: "${IMAGE:?image repository is required}"
: "${GITHUB_REPOSITORY:?GitHub repository is required}"

RELEASE="$(gh api --paginate "repos/$GITHUB_REPOSITORY/releases?per_page=100" | jq -r '
  .[] | select(.draft == false and .prerelease == false)
  | select(.tag_name | test("^v[0-9]+\\.[0-9]+\\.[0-9]+$"))
  | .tag_name as $version
  | (.body // "") | capture("Digest: (?<digest>sha256:[a-f0-9]{64})(?:[^a-f0-9]|$)")
  | [$version, .digest] | @tsv
' | sort -k1,1V | tail -n 1)"
if [ -z "$RELEASE" ]; then
  echo "no completed release with a recorded digest in $GITHUB_REPOSITORY" >&2
  exit 1
fi
read -r VERSION DIGEST <<< "$RELEASE"
skopeo copy --all --preserve-digests "docker://$IMAGE@$DIGEST" "docker://$IMAGE:latest"
PUBLISHED="sha256:$(skopeo inspect --raw "docker://$IMAGE:latest" | sha256sum | cut -d ' ' -f 1)"
if [ "$PUBLISHED" != "$DIGEST" ]; then
  echo "$IMAGE:latest serves $PUBLISHED, expected $VERSION digest $DIGEST" >&2
  exit 1
fi
echo "$IMAGE:latest -> $VERSION ($DIGEST)"
