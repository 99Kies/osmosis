//go:build ignore

package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"

	tmproto "github.com/cometbft/cometbft/proto/tendermint/types"
	tm "github.com/cometbft/cometbft/types"
	"github.com/cosmos/cosmos-sdk/codec/unknownproto"
	clienttypes "github.com/cosmos/ibc-go/v8/modules/core/02-client/types"
	ibctm "github.com/cosmos/ibc-go/v8/modules/light-clients/07-tendermint"
)

func run() error {
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		return err
	}
	var f struct {
		LightBlock []byte `json:"light_block"`
	}
	if err = json.Unmarshal(raw, &f); err != nil {
		return err
	}
	var p tmproto.LightBlock
	if err = p.Unmarshal(f.LightBlock); err != nil {
		return err
	}
	// Stock falsely sees an empty key oneof after skipping the unfamiliar tag.
	fmt.Printf("protobuf key=%T signature_bytes=%d\n", p.ValidatorSet.Validators[0].PubKey.Sum, len(p.SignedHeader.Commit.Signatures[0].Signature))
	h := ibctm.Header{SignedHeader: p.SignedHeader, ValidatorSet: p.ValidatorSet, TrustedValidators: p.ValidatorSet, TrustedHeight: clienttypes.NewHeight(0, uint64(p.SignedHeader.Header.Height-1))}
	verify := func() error {
		if err = h.ValidateBasic(); err != nil {
			return fmt.Errorf("IBC Header.ValidateBasic: %w", err)
		}
		wire, err := h.Marshal()
		if err != nil {
			return err
		}
		if err := unknownproto.RejectUnknownFieldsStrict(wire, &h, unknownproto.DefaultAnyResolver{}); err != nil {
			return fmt.Errorf("SDK strict protobuf decode: %w", err)
		}
		signed, err := tm.SignedHeaderFromProto(h.SignedHeader)
		if err != nil {
			return err
		}
		vset, err := tm.ValidatorSetFromProto(h.ValidatorSet)
		if err != nil {
			return err
		}
		return vset.VerifyCommitLight(signed.ChainID, signed.Commit.BlockID, signed.Height, signed.Commit)
	}
	if err := verify(); err != nil {
		return err
	}
	fmt.Println("PASS valid real Dora ML-DSA-65 header and commit")
	sig := p.SignedHeader.Commit.Signatures[0].Signature
	sig[len(sig)/2] ^= 1
	if err := verify(); err == nil {
		return errors.New("mutated signature unexpectedly accepted")
	} else {
		fmt.Printf("PASS mutation rejected: %v\n", err)
	}
	return nil
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
