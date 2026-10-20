# Sluice

A gate for GenLayer contracts that need to trust another contract's answer. You declare a rule once: which contract, which view method, which arguments, and what the answer must look like. Sluice then says **open** or **closed, because ...**, using only that contract's **finalized** state and judging freshness on **your** clock.

Live on **GenLayer Bradbury Testnet** (chain 4221). One contract, no owner, rules can never be changed. How it works and the threats considered are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Contract | Address |
|---|---|
| Sluice | [`0xfE2eb041585B7FE25B92285Bb1062aB766C6615B`](https://explorer-bradbury.genlayer.com/address/0xfE2eb041585B7FE25B92285Bb1062aB766C6615B) |

## Why it exists

Contracts that read other contracts on GenLayer keep making the same four mistakes, and each one cost us a bug while building [Lading](https://github.com/2TheMoom/lading), [Ballast](https://github.com/2TheMoom/ballast) and the earlier escrows:

1. **Reading accepted state.** An accepted answer can still be appealed away. Only finalized state is safe to move money on.
2. **Taking an answer on faith.** A field that is `1` instead of `true`, a string where a number belongs, a missing key.
3. **Judging freshness on the other contract's clock.** A finalized read does not give you a clock you can rely on.
4. **Treating "could not read it" as a yes**, or as a no that triggers a refund.

Sluice is the careful version of that read, written once.

## Using it

Declare a rule. It is stored once and can never be changed:

```
create_rule(rule_id, target, method, args, must_true, must_false, fresh_field, max_age, min_field, min_value)
```

For example, "Ballast monitor `weth-eth` must exist, be covered and not be disputed, its attestation time `at` must be under a day old, and its ratio at least 10,000 basis points":

```js
await client.writeContract({ address: SLUICE, functionName: "create_rule", args: [
  "weth-covered", BALLAST, "get_latest", JSON.stringify(["weth-eth"]),
  "exists,covered", "disputed", "at", 86400n, "ratio_bps", 10000n,
] });
```

Ask it, from a script or from another contract:

```python
v = gl.get_contract_at(SLUICE).view(state=StorageType.LATEST_FINAL).check_pinned("weth-covered", EXPECTED_HASH, my_now)
if not v["open"]:
    raise gl.vm.UserError("Refused: " + v["code"] + " " + v["field"])
```

`check` returns `{open, code, field, message, age}`. The codes are `ok`, `unreadable`, `bad_shape`, `missing`, `wrong_type`, `not_true`, `not_false`, `stale`, `future` and `below_min`. `check_pinned` additionally refuses unless the rule has exactly the hash you expect, so a lookalike rule id cannot stand in for the one you audited. `record(rule_id)` stores the verdict on the transaction's clock so an explorer shows it. A small consumer, [contracts/test_support/sluice_consumer.py](contracts/test_support/sluice_consumer.py), shows the pattern and is what the live run uses.

What a rule can say: fields that must be `true`, fields that must be `false`, one timestamp field that must be no older than `max_age`, and one whole-number field that must be at least `min_value`. A rule with no condition is refused, so no rule is ever open for everything. Method and field names are plain lowercase names that start with a letter, arguments are at most four strings or whole numbers, and the target can never be Sluice itself.

### Known limits you must design for

- **A target that is not finalized yet aborts its caller.** If the contract being read was deployed too recently to be final, GenVM aborts the contract that asked (`invalid_contract absent_runner_comment`) and a Python `try` cannot catch it. Rules should point at contracts that have finalized.
- **A new fact takes about 30 minutes to be visible**, because only finalized state is read.
- **A recorded verdict is only as fresh as its `last_at`.** Consumers should call `check` with their own clock.
- Sluice cannot tell you the target is honest, only that its finalized answer meets the rule.

## Security model

The threat list was written before the code and each row is tested. It is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

| Test type | Command | What it covers |
|---|---|---|
| Lint | `genvm-lint check contracts/sluice.py` | static checks |
| Direct | `python -m pytest tests/direct/ -v` | 38 in-memory tests with a fake target that records the address, method, arguments and storage state it was asked for |
| Live | `SLUICE=0x... node scripts/e2e.mjs` | the Bradbury run described below, against the real Ballast contract |
| Mutation | `python scripts/mutate.py` | breaks the contract 66 ways, one at a time, and checks a test notices |

Mutation result: 65 of 66 breakages are caught. The one survivor is equivalent: dropping the check that a page size is at least one changes nothing, because a page of zero ids is empty either way.

<!--LIVE_START-->
## Live evidence (Bradbury, run `v1`, 2026-10-20)

Sluice `0xfE2eb041585B7FE25B92285Bb1062aB766C6615B`, reading the real, deployed Ballast contract `0xd1590EBBA44Cd2d00e003053811E47b9d2209668` (finalized state only). Seven rules over real monitors, each answered by Sluice with the caller's own clock, then recorded on chain, and the recorded verdict read back and compared. The run fails if any answer is not exactly the expected one.

| Rule | What it asks of Ballast | Sluice's answer | Created | Recorded |
|---|---|---|---|---|
| `covered-30d` | monitor `e2e-v2-covered` exists, is covered, is not disputed, and was attested within 30 days | **open**, `ok`, age 92,843 s | [`0xd859ec1909`](https://explorer-bradbury.genlayer.com/tx/0xd859ec190965c73918141d5b94a2000166f7c0346cb133f0c6d29fcca93a998e) | [`0x061941a7fa`](https://explorer-bradbury.genlayer.com/tx/0x061941a7fadb04449aa1f538c6ffd2868421ef1897e94754ae5db18a421e8bc7) |
| `covered-1h` | the same monitor, but the attestation must be under one hour old | closed, `stale` on `at`, age 92,843 s | [`0x24ca6204ff`](https://explorer-bradbury.genlayer.com/tx/0x24ca6204ff686a8594c42f226ed6237c54e0d408dba26b1afbdff0d0d887fd3f) | [`0xf9f27c9639`](https://explorer-bradbury.genlayer.com/tx/0xf9f27c9639951d65c5c999f1a8a0a04f30d6ff005d5652c64d73c93196373d22) |
| `strict` | monitor `e2e-v2-strict` (a 101% floor, attested short) must be covered | closed, `not_true` on `covered` | [`0x0310af1585`](https://explorer-bradbury.genlayer.com/tx/0x0310af158521545e6a737ebffdcea871c3b5abbc8bcd2a2f829b7658b6ee3024) | [`0x3c5df6eeb0`](https://explorer-bradbury.genlayer.com/tx/0x3c5df6eeb0dc41ca52fb6e6ba45a6296ef3d7ff55482bcaebb9891a3c3a0ae9e) |
| `floor-ok` | monitor `e2e-v2-twochains` is covered and its ratio is at least 11,000 bps | **open**, `ok`, age 92,899 s | [`0x39634aeb3f`](https://explorer-bradbury.genlayer.com/tx/0x39634aeb3ffbe3d082137491d7a0d17fd6e9bed11795fc888f390c6ed354708b) | [`0x8d4b9da065`](https://explorer-bradbury.genlayer.com/tx/0x8d4b9da065eda64d6b33085b08ad900332b3aefe864bf0d5b525bfbbf9883df6) |
| `floor-high` | the same monitor with a floor of 12,000 bps | closed, `below_min` on `ratio_bps`, age 92,899 s | [`0xa3eded006b`](https://explorer-bradbury.genlayer.com/tx/0xa3eded006b7e58054d6f6c7ac8762b196be7edc7978b3a855d577bcb837a5b2e) | [`0x5aeb1d9e0e`](https://explorer-bradbury.genlayer.com/tx/0x5aeb1d9e0e6c90753bcb7c532727ab08b016d66339bd05f967e9059279eb1dec) |
| `no-monitor` | a monitor id that does not exist | closed, `unreadable` | [`0x15e87b4d7a`](https://explorer-bradbury.genlayer.com/tx/0x15e87b4d7a9c325b7c00544ca3aee7f0a6cc55544bd53d9b637de8d81aaf05a4) | [`0xcdf1502415`](https://explorer-bradbury.genlayer.com/tx/0xcdf150241540783ec68b4faddcd83a3793b978ac911e7edc01aea6f6840ecd4b) |
| `real-wallet` | monitor `mm-weth-base-1` (the real-MetaMask run) is covered, fresh within a day, ratio at least 10,000 bps | **open**, `ok`, age 30,385 s | [`0x304a318c55`](https://explorer-bradbury.genlayer.com/tx/0x304a318c558d13788bade5be2b72ca0d9e3ed2c25c222b7d1ea0bbcb53fc6421) | [`0x8470c2973a`](https://explorer-bradbury.genlayer.com/tx/0x8470c2973a8dd8e05f8efa9d33476508bac12259983901710611eaadc3a2b856) |

Things the table shows: the same finalized data is open under a 30 day limit and closed under a one hour limit (the age is judged on the caller's clock), a short monitor is refused with the field that failed, a floor is inclusive and reported as `below_min`, and a monitor that cannot be read closes the gate instead of opening it. Ballast raises "Monitor not found" for an unknown id, and Sluice reports exactly that as `unreadable`.

Bad calls were refused with the contract's own reason: a rule hash that is not the expected one, a negative clock, and an unknown rule. A pinned check with the right hash was open.

### A contract reading the gate in finalized state

A nested read: a consumer contract asks Sluice (finalized state), and Sluice reads Ballast (finalized state). It ran only after the rules had FINALIZED, which took about 26 minutes.

- Open rule `covered-30d`: consumer [`0x53c26A3c45574482Dc210f339bB9E9e5a4fDcdD4`](https://explorer-bradbury.genlayer.com/address/0x53c26A3c45574482Dc210f339bB9E9e5a4fDcdD4) acted ([`0xb200db4662`](https://explorer-bradbury.genlayer.com/tx/0xb200db466228b0a9689c78afb9c0754ea6d75d92f27afcc544bcde827431a9ae)), `performed = 1`.
- Closed rule `strict`: consumer [`0x0f60e8930bE235fBF55f5E91BaCD058C19d17808`](https://explorer-bradbury.genlayer.com/address/0x0f60e8930bE235fBF55f5E91BaCD058C19d17808) was refused with Sluice's own reason, `Refused: not_true covered` ([`0x69f7d5d292`](https://explorer-bradbury.genlayer.com/tx/0x69f7d5d29248ec66c5343349e890a66fbd9ccf96a63375d574dc69c3716844b0)).

Both consumers are pinned to the rule's hash, so a lookalike rule id could not stand in for the audited one.

A note on honesty about the run itself: the first attempt stopped after the fourth `record` because the script read the rule back before the node had caught up with the accepted write, so it saw `records = 0`. That is a script timing error, not a contract one. The script now polls the read-back and can resume from its own log, and the resumed run is the one above (the full event log, with every transaction hash, is `scripts/e2e-log-v1.json`).

<!--LIVE_END-->

## Layout

- `contracts/sluice.py`, `contracts/test_support/sluice_consumer.py`
- `tests/direct/test_sluice.py`
- `scripts/deploy.mjs`, `scripts/e2e.mjs` (the live run), `scripts/mutate.py`
- `docs/ARCHITECTURE.md`

## License

MIT
