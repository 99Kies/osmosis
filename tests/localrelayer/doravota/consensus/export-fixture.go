//go:build ignore

package main

import (
	"encoding/json"
	"fmt"
	"os"

	cmtjson "github.com/cometbft/cometbft/libs/json"
	ctypes "github.com/cometbft/cometbft/rpc/core/types"
	tm "github.com/cometbft/cometbft/types"
)

func must(err error) {
	if err != nil {
		panic(err)
	}
}
func read(path string, out any) {
	raw, err := os.ReadFile(path)
	must(err)
	must(cmtjson.Unmarshal(raw, out))
}
func main() {
	if len(os.Args) != 3 {
		panic("usage: export-fixture commit.json validators.json")
	}
	var commit ctypes.ResultCommit
	var vals ctypes.ResultValidators
	read(os.Args[1], &commit)
	read(os.Args[2], &vals)
	vset := tm.NewValidatorSet(vals.Validators)
	block := &tm.LightBlock{SignedHeader: &commit.SignedHeader, ValidatorSet: vset}
	must(block.ValidateBasic(commit.Header.ChainID))
	must(vset.VerifyCommitLight(commit.Header.ChainID, commit.Commit.BlockID, commit.Header.Height, commit.Commit))
	p, err := block.ToProto()
	must(err)
	wire, err := p.Marshal()
	must(err)
	must(json.NewEncoder(os.Stdout).Encode(map[string]any{
		"light_block":     wire,
		"address":         fmt.Sprintf("%X", vset.Validators[0].Address.Bytes()),
		"validator_hash":  fmt.Sprintf("%X", vset.Hash()),
		"public_key":      vset.Validators[0].PubKey.Bytes(),
		"vote_sign_bytes": commit.Commit.VoteSignBytes(commit.Header.ChainID, 0),
		"signature":       commit.Commit.Signatures[0].Signature,
	}))
}
