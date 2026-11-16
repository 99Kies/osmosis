#!/usr/bin/env bash
set -euo pipefail
osm_src=$(cd "$1" && pwd)
out=$(cd "$2" && pwd)
recipe=$(cd "$(dirname "$0")" && pwd)
export GOTOOLCHAIN=go1.26.10
cd "$osm_src"
fork=github.com/osmosis-labs/cometbft@v0.38.17-v28-osmo-1
if [[ ! -d "$out/vendor/cometbft-osmo" ]]; then
  src=$(go mod download -json "$fork" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Dir"])')
  mkdir -p "$out/vendor"
  cp -a "$src" "$out/vendor/cometbft-osmo"
  chmod -R u+w "$out/vendor/cometbft-osmo"
  (cd "$out/vendor/cometbft-osmo" && git apply "$recipe/cometbft-osmo-ml-dsa.patch"
   go mod edit -require=github.com/cloudflare/circl@v1.6.3)
fi
# Preserve the original workspace graph, including local Osmosis submodules.
export GOWORK="$out/osmo-pq.go.work"
python3 - "$osm_src" "$GOWORK" <<'PY'
from pathlib import Path
import sys,re
src,out=map(Path,sys.argv[1:])
text=(src/"go.work").read_text()
text=re.sub(r"(?m)^use (\.[^\n]*)$",lambda m:'use "'+str((src/m[1]).resolve())+'"',text)
Path(out).write_text(text)
PY
if [[ -f go.work.sum ]]; then cp go.work.sum "$GOWORK.sum"; fi
go work edit -replace="github.com/cometbft/cometbft=$out/vendor/cometbft-osmo"
go mod download github.com/cloudflare/circl@v1.6.3
go build -p 4 -mod=readonly -ldflags "-X github.com/cosmos/cosmos-sdk/version.Version=ibc-consensus-ml-dsa -X github.com/cosmos/cosmos-sdk/version.Commit=$(git rev-parse HEAD)" -o "$out/bin/osmosisd-pq.tmp" ./cmd/osmosisd
mv "$out/bin/osmosisd-pq.tmp" "$out/bin/osmosisd-pq"
