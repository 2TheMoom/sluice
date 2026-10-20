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
<!--LIVE_END-->

## Layout

- `contracts/sluice.py`, `contracts/test_support/sluice_consumer.py`
- `tests/direct/test_sluice.py`
- `scripts/deploy.mjs`, `scripts/e2e.mjs` (the live run), `scripts/mutate.py`
- `docs/ARCHITECTURE.md`

## License

MIT
