//go:build ignore

package main

import (
	"fmt"
	"github.com/cometbft/cometbft/crypto/mldsa65"
	"github.com/cometbft/cometbft/libs/json"
	"github.com/cometbft/cometbft/privval"
	"os"
	"path/filepath"
)

func main() {
	if len(os.Args) != 2 {
		panic("expected a fresh node home")
	}
	home := os.Args[1]
	state := filepath.Join(home, "data", "priv_validator_state.json")
	raw, err := os.ReadFile(state)
	if err != nil {
		panic(err)
	}
	var signState privval.FilePVLastSignState
	if err = json.Unmarshal(raw, &signState); err != nil || signState.Height != 0 || signState.Round != 0 || signState.Step != 0 || len(signState.Signature) != 0 || len(signState.SignBytes) != 0 {
		panic("validator state is not fresh")
	}
	priv, err := mldsa65.GenPrivKey()
	if err != nil {
		panic(err)
	}
	pv := privval.NewFilePV(priv, filepath.Join(home, "config", "priv_validator_key.json"), state)
	pv.Save()
	fmt.Println(pv.GetAddress())
}
