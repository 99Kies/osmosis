#!/usr/bin/env python3
"""Run one isolated 3-chain ICS-20 matrix on the experiment host."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request

os.umask(0o077)
lab_path, phase, osmo_bin, hermes_bin, *options = sys.argv[1:]
lab = Path(lab_path).resolve()
assert phase in ("baseline", "updated", "rehearsal", "control")
run = lab / phase
run.mkdir()  # Never reuse an existing chain home or key store.
evidence = run / "evidence"
evidence.mkdir()
env = dict(os.environ, HOME=str(run), RUST_LOG="info")
hermes = lab / "bin" / hermes_bin
config = run / "hermes.toml"
processes = []
chains = {}


def log(*values):
    print(*values, flush=True)


def cli(args, *, user_input=None, timeout=120, include_stderr=False):
    p = subprocess.run([str(a) for a in args], input=user_input, text=True,
                       capture_output=True, env=env, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"{args[:5]} exit={p.returncode}\n{p.stdout[-8000:]}\n{p.stderr[-8000:]}")
    return ((p.stderr + "\n") if include_stderr else "") + p.stdout.strip()


def cli_json(args, required_field):
    raw = cli(args, include_stderr=True)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\s*(?=\{)", raw):
        try:
            item, _ = decoder.raw_decode(raw, match.end())
        except ValueError:
            continue
        if isinstance(item, dict) and required_field in item:
            return item
    raise ValueError(f"{args[:5]} did not emit JSON with {required_field!r}: {raw[-1200:]}")


def hcli(*args):
    common = [hermes, "--config", config]
    return cli([*common, "--json", *args], timeout=300, include_stderr=True)


def hjson(raw):
    for line in reversed(raw.splitlines()):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and "status" in payload:
            if payload["status"] != "success":
                raise RuntimeError(raw)
            return payload["result"]
    raise ValueError(raw)


def rpc(port, method, **params):
    url = f"http://127.0.0.1:{port}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=4) as response:
        raw = json.load(response)
    if "error" in raw:
        raise RuntimeError(raw["error"])
    return raw["result"]


def save(name, data):
    if not isinstance(data, str):
        data = json.dumps(data, indent=2)
    (evidence / name).write_text(data + "\n")


def wait(fn, seconds, name):
    end = time.monotonic() + seconds
    error = None
    while time.monotonic() < end:
        try:
            result = fn()
            if result:
                return result
        except Exception as exc:
            error = exc
        time.sleep(1)
    raise RuntimeError(f"timeout: {name}: {error}")


def edit_toml(path, changes):
    sections = set()
    output = []
    current = ""
    for line in path.read_text().splitlines():
        section = re.fullmatch(r'\[([^]]+)\]', line)
        if section:
            current = section.group(1)
        key = re.match(r"^([a-zA-Z0-9_-]+)\s*=", line)
        if key and (current, key[1]) in changes:
            item = (current, key[1])
            sections.add(item)
            line = f"{key[1]} = {changes[item]}"
        output.append(line)
    assert sections == changes.keys(), (path, changes.keys() - sections)
    path.write_text("\n".join(output) + "\n")


def node(chain, binary, prefix, denom, offset):
    home = run / chain
    binpath = lab / "bin" / binary
    ports = dict(rpc=26657+offset, p2p=26656+offset, grpc=9090+offset,
                 abci=26658+offset, api=1317+offset, grpc_web=9091+offset, pprof=6060+offset)
    d = [binpath, "--home", home]
    keys = [*d, "keys", "--keyring-backend", "test"]
    cli([*d, "init", chain, "--chain-id", chain])
    algo = ["--key-type", "ml_dsa_65"]
    signer_uses_mldsa = prefix == "dora"
    funds = 100 * 10**18 if prefix == "dora" else 10**12
    for name in ["validator", "relayer", "user", "receiver"]:
        flags = algo if name in ("user", "receiver") and signer_uses_mldsa else []
        raw = cli([*keys, "add", name, "--output", "json", *flags], include_stderr=True)
        j = json.loads(next(line for line in raw.splitlines() if line.startswith("{")))
        if flags:
            key_type = j["pubkey"]
            assert "mldsa65" in str(key_type), key_type
        if name == "relayer":
            (home / "relayer.mnemonic").write_text(j["mnemonic"] + "\n")
        cli([*d, "add-genesis-account", j["address"], f"{funds}{denom}"])
    gen = home / "config/genesis.json"
    genesis = json.loads(gen.read_text())
    genesis["app_state"]["staking"]["params"]["bond_denom"] = denom
    if prefix == "osmo" and (True):
        # Osmosis validates fees against the txfees base denom, independently
        # of the staking denom and node minimum gas prices.
        genesis["app_state"]["txfees"]["basedenom"] = denom
    if prefix == "osmo":
        save(f"{chain}-fee-genesis.json", {
            "staking_bond_denom": denom,
            "txfees_base_denom": genesis["app_state"]["txfees"]["basedenom"],
            "relayer_fee_denom": denom,
        })
    params = genesis["consensus"]["params"] if "consensus" in genesis else genesis["consensus_params"]
    pk_types = params["validator"]["pub_key_types"]
    assert pk_types == ["ed25519"], pk_types
    expected_validator = "ml_dsa_65" if chain == "dorad-a" else "ed25519"
    if expected_validator == "ml_dsa_65":
        cli([lab / "bin/gen-validator", home])
        params["validator"]["pub_key_types"] = [expected_validator]
    gen.write_text(json.dumps(genesis))
    cli([*d, "gentx", "validator", f"{funds // 2}{denom}", "--chain-id", chain,
         "--keyring-backend", "test"])
    cli([*d, "collect-gentxs"])
    edit_toml(home / "config/config.toml", {
        ("", "proxy_app"): f'"tcp://127.0.0.1:{ports["abci"]}"',
        ("rpc", "laddr"): f'"tcp://127.0.0.1:{ports["rpc"]}"',
        ("rpc", "pprof_laddr"): '""',
        ("p2p", "laddr"): f'"tcp://127.0.0.1:{ports["p2p"]}"',
        ("p2p", "pex"): "false",
        ("consensus", "timeout_commit"): '"1s"',
    })
    edit_toml(home / "config/app.toml", {
        ("", "minimum-gas-prices"): f'"0.025{denom}"',
        ("grpc", "address"): f'"127.0.0.1:{ports["grpc"]}"',
        ("api", "address"): f'"tcp://127.0.0.1:{ports["api"]}"',
    })
    with (run / f"{chain}.log").open("w") as f:
        proc = subprocess.Popen([*map(str,d), "start"], stdout=f, stderr=subprocess.STDOUT, env=env)
    processes.append(proc)

    def healthy():
        if proc.poll() is not None:
            raise RuntimeError((run / f"{chain}.log").read_text()[-6000:])
        status = rpc(ports["rpc"], "status")
        return status if int(status["sync_info"]["latest_block_height"]) >= 3 else None

    save(f"{chain}-status.json", wait(healthy, 45, chain))
    val = rpc(ports["rpc"], "validators")
    expected_rpc_key = "PubKeyMlDsa65" if expected_validator == "ml_dsa_65" else "PubKeyEd25519"
    assert len(val["validators"]) == 1 and val["validators"][0]["pub_key"]["type"].endswith(expected_rpc_key), val
    commit = rpc(ports["rpc"], "commit")
    sig = next(v["signature"] for v in commit["signed_header"]["commit"]["signatures"] if v["signature"])
    assert len(base64.b64decode(sig)) == (3309 if chain == "dorad-a" else 64)
    save(f"{chain}-commit.json", commit)
    save(f"{chain}-validators.json", val)
    save(f"{chain}-version.txt", cli([*d, "version", "--long"]))
    addr = {key: cli([*keys, "show", key, "-a"]) for key in ("user", "receiver", "relayer")}
    pub = cli([*keys, "show", "user", "--output", "json"], include_stderr=True)
    save(f"{chain}-user-pubkey.json", json.loads(next(line for line in pub.splitlines() if line.startswith("{"))))
    chains[chain] = (d, ports, prefix, denom, addr, home)
    log("ready", chain, expected_validator, addr["user"])


def configure(override=False):
    config.write_text("""[global]
log_level = "info"

[mode.clients]
enabled = true
refresh = true
misbehaviour = true

[mode.connections]
enabled = false

[mode.channels]
enabled = false

[mode.packets]
enabled = true
clear_interval = 0
clear_on_start = false
tx_confirmation = true
""")
    with config.open("a") as f:
        for chain, (_,p,prefix,denom,_,_) in chains.items():
            f.write(f'''
[[chains]]
id = "{chain}"
type = "CosmosSdk"
rpc_addr = "http://127.0.0.1:{p["rpc"]}"
grpc_addr = "http://127.0.0.1:{p["grpc"]}"
event_source = {{ mode = "push", url = "ws://127.0.0.1:{p["rpc"]}/websocket", batch_delay = "200ms" }}
rpc_timeout = "12s"
account_prefix = "{prefix}"
key_name = "relayer"
store_prefix = "ibc"
max_gas = 4000000
gas_multiplier = 1.5
gas_price = {{ price = 0.035, denom = "{denom}" }}
clock_drift = "10s"
max_block_time = "5s"
''')
            if prefix == "dora" and override:
                f.write('compat_mode = "0.38"\n')


def connect(a, b):
    log("handshake", a, b)
    raw = hcli("create", "channel", "--a-chain", a, "--b-chain", b,
               "--a-port", "transfer", "--b-port", "transfer", "--new-client-connection", "--yes")
    save(f"{a}-{b}-channel.jsonl", raw)
    ch = hjson(raw)
    return ch["a_side"]["channel_id"], ch["b_side"]["channel_id"]


def length_fields(data):
    """Read the length-delimited fields of TxRaw, AuthInfo, SignerInfo and Any."""
    def varint():
        nonlocal i
        value = 0
        for shift in range(0, 70, 7):
            byte = data[i]
            i += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("oversized protobuf varint")

    i = 0
    while i < len(data):
        tag = varint()
        wire_type = tag & 7
        if wire_type == 0:
            varint()
        elif wire_type == 2:
            size = varint()
            end = i + size
            if end > len(data):
                raise ValueError("truncated protobuf field")
            yield tag >> 3, data[i:end]
            i = end
        elif wire_type in (1, 5):
            i += {1: 8, 5: 4}[wire_type]
        else:
            raise ValueError(f"unexpected protobuf wire type: {wire_type}")


def signer(tx):
    fields = dict(length_fields(base64.b64decode(tx["tx"])))
    auth = dict(length_fields(fields[2]))
    info = dict(length_fields(auth[1]))
    pubkey = dict(length_fields(info[1]))
    return {"type_url": pubkey[1].decode(), "signature_bytes": len(fields[3])}


def transfer(a, b, ca, cb):
    label = f"{a}-to-{b}"
    log("transfer", label, ca, cb)
    d, ports, prefix, denom, _, _ = chains[a]
    dest, dp, _, _, address, _ = chains[b]
    amount = 177
    broadcast = cli([*d, "tx", "ibc-transfer", "transfer", "transfer", ca,
                     address["receiver"], f"{amount}{denom}", "--from", "user",
                     "--node", f"tcp://127.0.0.1:{ports['rpc']}", "--chain-id", a,
                     "--keyring-backend", "test", "--sign-mode", "direct", "--gas", "350000",
                     "--fees", f"14000{denom}", "--broadcast-mode", "sync", "-y", "-o", "json"])
    tx = json.loads(broadcast)
    save(f"{label}-checktx.json", tx)
    assert int(tx.get("code", 0)) == 0, tx
    confirmed = wait(lambda: rpc(ports["rpc"], "tx", hash="0x"+tx["txhash"]), 30, label)
    save(f"{label}-send.json", confirmed)
    assert confirmed["tx_result"].get("code", 0) == 0, confirmed
    sender = signer(confirmed)
    expected = ("/cosmos.crypto.mldsa65.PubKey", 3309) if prefix == "dora" else ("/cosmos.crypto.secp256k1.PubKey", 64)
    assert (sender["type_url"], sender["signature_bytes"]) == expected, sender
    save(f"{label}-sender-algorithm.json", sender)
    packet = next(e for e in confirmed["tx_result"]["events"] if e["type"] == "send_packet")
    seq = next(i["value"] for i in packet["attributes"] if i["key"] == "packet_sequence")
    # Query stored acknowledgement and prove the commitment was consumed on the sender.
    def check_ack():
        rows = rpc(dp["rpc"], "tx_search",
                   query=f'"write_acknowledgement.packet_dst_channel=\'{cb}\' AND write_acknowledgement.packet_sequence=\'{seq}\'"')
        return rows["txs"]
    rows = wait(check_ack, 120, label + " receive and acknowledgement")
    save(f"{label}-receive.json", rows)
    assert rows[0]["tx_result"].get("code", 0) == 0, rows
    event = next(e for e in rows[0]["tx_result"]["events"] if e["type"] == "write_acknowledgement"
                 and {x["key"]:x["value"] for x in e["attributes"]}.get("packet_sequence") == seq)
    contents = {i["key"]: i["value"] for i in event["attributes"]}
    ack = json.loads(bytes.fromhex(contents["packet_ack_hex"]) if "packet_ack_hex" in contents
                     else contents["packet_ack"])
    assert base64.b64decode(ack["result"], validate=True) == b"\x01", ack
    def check_source_ack():
        return rpc(ports["rpc"], "tx_search",
                   query=f'"acknowledge_packet.packet_src_channel=\'{ca}\' AND acknowledge_packet.packet_sequence=\'{seq}\'"')["txs"]
    ack_tx = wait(check_source_ack, 120, label + " source ack")
    save(f"{label}-ack.json", ack_tx)
    assert ack_tx[0]["tx_result"].get("code", 0) == 0, ack_tx
    for row in (rows[0], ack_tx[0]):
        relay = signer(row)
        assert (relay["type_url"], relay["signature_bytes"]) == ("/cosmos.crypto.secp256k1.PubKey", 64), relay
    voucher = "ibc/" + hashlib.sha256(f"transfer/{cb}/{denom}".encode()).hexdigest().upper()
    balance = cli_json([*dest, "query", "bank", "balances", address["receiver"],
                       "--node", f"tcp://127.0.0.1:{dp['rpc']}", "-o", "json"], "balances")
    save(f"{label}-balance.json", balance)
    assert next(int(x["amount"]) for x in balance["balances"] if x["denom"] == voucher) == amount
    log("PASS", label, "packet", seq, "send", tx["txhash"], "receive", rows[0]["hash"],
        "ack", ack_tx[0]["hash"], "balance", amount, voucher)


def main():
    suffix = "-new" if phase == "updated" else ""
    port_shift = {"baseline": 0, "updated": 100, "rehearsal": 200, "control": 300}[phase]
    a, b, o = "dorad-a", "dorad-b", "osmosis" + suffix
    node(a, "dorad", "dora", "peaka", 1000 + port_shift)
    node(o, osmo_bin, "osmo", "uosmo", 3000 + port_shift)
    if "nodes-only" in options:
        return
    save("hermes-version.txt", cli([hermes, "version"]))
    configure()
    health = hcli("health-check")
    save("health-auto.jsonl", health)
    if phase == "updated":
        assert health.count('"message":"chain is healthy"') == len(chains), health
    for chain, (_, _, _, _, _, home) in chains.items():
        save(f"{chain}-hermes-key.jsonl", hcli("keys", "add", "--chain", chain,
                                             "--mnemonic-file", home/"relayer.mnemonic"))
    channels = []
    errors = []
    if "override" in options:
        configure(True)
        save("health-override.jsonl", hcli("health-check"))
    for pair in [(a,o)]:
        try:
            channels.append((*pair, *connect(*pair)))
        except Exception as exc:
            errors.append(str(exc))
            save(f"{pair[0]}-{pair[1]}-channel.error", str(exc))
            log("FAIL handshake", *pair, str(exc)[-5000:])
    with (run/"hermes.log").open("w") as f:
        processes.append(subprocess.Popen([str(hermes), "--config", str(config), "start"],
                                          stdout=f, stderr=subprocess.STDOUT, env=env))
    time.sleep(4)
    for one, two, ch_one, ch_two in channels:
        for src, dst, sc, dc in [(one,two,ch_one,ch_two), (two,one,ch_two,ch_one)]:
            try:
                transfer(src, dst, sc, dc)
            except Exception as exc:
                errors.append(str(exc))
                save(f"{src}-to-{dst}.error", traceback.format_exc())
                log("FAIL transfer", src, dst, exc)
    if errors:
        raise RuntimeError(f"{len(errors)} failed test case(s)")


try:
    main()
finally:
    for proc in reversed(processes):
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
