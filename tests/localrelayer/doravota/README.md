# Dora Vota v1.1.0 ICS-20 regression

This runs two isolated Dora Vota chains and one Osmosis chain on loopback.
The Dora senders use native ML-DSA-65 accounts, all validators use Ed25519,
and Hermes submits the IBC transactions with independent secp256k1 accounts.
It exercises the transfer wire contract without adding account-signature
verification to the destination chain.

On a Linux test host with Python 3.10+, build the target revisions and stage
their executables as `$LAB/bin/dorad`, `$LAB/bin/osmosisd`, and `$LAB/bin/hermes`.
The Dora binary must be built from tag `v1.1.0`. Run:

```sh
python3 tests/localrelayer/doravota/run.py "$LAB" baseline osmosisd hermes
```

The `baseline` phase keeps the default Osmosis `txfees.basedenom` when changing
the staking denom to `uosmo`. This reproduces the Hermes `invalid fee token`
failure on a fresh default Osmosis genesis. The `control` phase aligns the
genesis fee denom to `uosmo` while using the same baseline binaries:

```sh
python3 tests/localrelayer/doravota/run.py "$LAB" control osmosisd hermes
```

To test a second pair of binaries from clean state, stage them as
`$LAB/bin/osmosisd-new` and `$LAB/bin/hermes-new`, then run:

```sh
python3 tests/localrelayer/doravota/run.py "$LAB" updated osmosisd-new hermes-new
```

The `updated` phase also aligns the fee denom and requires healthy Hermes diagnostics.

The script creates fresh test keys and dedicated chain homes under
`$LAB/<phase>`. A phase directory must not exist before running it.
Only test fixtures should use the test keyring. Share the phase's `evidence/`
directory, never the chain homes or mnemonic files. The script shuts down
the nodes it starts and retains chain data and logs for diagnosis.

Both Dora A ↔ Dora B and Dora A ↔ Osmosis run over new `ics20-1` channels.
Each direction checks source transaction execution and its on-wire
account signature type, destination transaction and successful
acknowledgement, the acknowledgement transaction on the source, and the
recipient's exact ICS-20 voucher balance. The packet mode uses live WebSocket
events with periodic packet clearing disabled.

To explicitly test the legacy Hermes `compat_mode = "0.38"` override for Dora,
use an empty lab phase and pass `override` after the Hermes binary name.
The override changes the RPC layout only. This test does not cover ML-DSA
validator keys, ML-DSA relayer keys, live chain upgrades, or PQCAuth enrollment.

For the separate ML-DSA validator experiment, see [consensus verification](consensus/README.md).
