# Dora A ML-DSA-65 validator regression

This experiment builds Dora Vota v1.1.0 from its release tag and creates a new
ML-DSA-65 validator for Dora A only. Osmosis validators remain Ed25519 and
relayer accounts remain secp256k1. Use a fresh lab directory and the test
keyrings only. The test never rotates an existing validator.

Build the key generator in the Dora source module and place it at
`$LAB/bin/gen-validator`:

```sh
go build -mod=readonly -o "$LAB/bin/gen-validator" /path/to/gen-validator.go
```

Stage the pinned unmodified binaries named `dorad`, `hermes` and `osmosisd` in
`$LAB/bin`. These commands each need a fresh phase directory:

```sh
python3 run.py "$LAB" baseline osmosisd hermes
python3 run.py "$LAB" control osmosisd hermes-pq
python3 run.py "$LAB" updated osmosisd-pq hermes-pq
```

The first two commands are expected to fail: the unmodified Hermes cannot
decode the Dora validator public key, and unmodified Osmosis cannot validate
Dora's signed header. All phases align the Osmosis fee denomination, so this
does not reproduce the earlier unrelated fee failure.

Build the patched Osmosis binary with
`build-osmosis.sh /path/to/osmosis "$LAB"`. It preserves the original Go
workspace, but substitutes a verification-only overlay on the **effective**
Osmosis CometBFT fork (`v0.38.17-v28-osmo-1`). This changes the remote commit
parser and validator-key codec and uses CIRCL for exact-size FIPS 204 signature
verification. It does not add a private key or allow a new local consensus key
in constants or parameters. The Hermes overlay is documented in its
`scripts/dora-ml-dsa` README.

Generate the Go reference fixture with `export-fixture.go` from the Dora module
using the `dorad-a-commit.json` and `dorad-a-validators.json` evidence files.
Run `probe-header.go` from the Osmosis module against the fixture with the stock
Go workspace, and then with `GOWORK="$LAB/osmo-pq.go.work"`. The probe checks
IBC header parsing, commit verification, and rejection of a mutated signature.

Only distribute evidence and public fixtures. Do not copy chain homes,
mnemonic files, or private validator keys.
