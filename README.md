# Sluice

A gate for GenLayer contracts that need to trust another contract's answer. You declare a rule once: which contract, which view method, which arguments, and what the answer must look like. Sluice then says **open** or **closed, because ...**, using only that contract's **finalized** state and judging freshness on **your** clock.

**Version 2 lets a rule carry one fact about the world.** Besides the on-chain conditions, a rule can name a public JSON page and a field that must read a given value. Anyone calls `observe`: the validators each fetch the page on their own and must agree on whether the field reads as the rule says, and the agreed answer is stored with its time. `check` stays deterministic and is open only while that observation is met and no older than the rule allows. See [An external fact](#an-external-fact-version-2).

Live on **GenLayer Bradbury Testnet** (chain 4221). One contract, no owner, rules can never be changed. How it works and the threats considered are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Contract | Address |
|---|---|
| Sluice v2 (the contract in this repo) | [`0xB914771C816E5c25d8757E3877244b4B501D6Ff5`](https://explorer-bradbury.genlayer.com/address/0xB914771C816E5c25d8757E3877244b4B501D6Ff5) |
| Sluice v2, the same source on Studionet (chain 61999) | [`0xDC5e08F20461D6d0158eb7E58Ea45965A02d1F25`](https://explorer-studio.genlayer.com/address/0xDC5e08F20461D6d0158eb7E58Ea45965A02d1F25) |
| Sluice v1 (no fact; its source is commit `7277b3f`) | [`0xfE2eb041585B7FE25B92285Bb1062aB766C6615B`](https://explorer-bradbury.genlayer.com/address/0xfE2eb041585B7FE25B92285Bb1062aB766C6615B) |

The v2 contract is the v1 contract plus the fact (`create_rule_with_fact`, `observe`); `create_rule`, `check`, `check_pinned` and `record` behave as before for a rule without a fact. The v1 live evidence further down is kept as it was.

Sluice reads other contracts in finalized state, and Studionet cannot answer that from inside a contract (a contract-to-contract read of finalized state never returns there; the probe is in the Lading repo, `docs/studionet-nested-final-read-probe.py`). So the full contract, including `check` over a contract target and consumers reading Sluice, runs on Bradbury. The identical source is also deployed on Studionet, where the validator flow (`create_rule_with_fact`, `observe`) runs and is evidenced below, but `check` on a rule with a contract target does not return. `scripts/verify-deployed.mjs` proves both deployments are byte for byte the contract in this repo.

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

`check` returns `{open, code, field, message, age}`. The codes are `ok`, `unreadable`, `bad_shape`, `missing`, `wrong_type`, `not_true`, `not_false`, `stale`, `future` and `below_min`, and for a rule with a fact `no_observation`, `observation_stale`, `observation_not_met` and `future` (field `observation`). `check_pinned` additionally refuses unless the rule has exactly the hash you expect, so a lookalike rule id cannot stand in for the one you audited. `record(rule_id)` stores the verdict on the transaction's clock so an explorer shows it. A small consumer, [contracts/test_support/sluice_consumer.py](contracts/test_support/sluice_consumer.py), shows the pattern and is what the live run uses.

What a rule can say: fields that must be `true`, fields that must be `false`, one timestamp field that must be no older than `max_age`, and one whole-number field that must be at least `min_value`. A rule with no condition is refused, so no rule is ever open for everything. Method and field names are plain lowercase names that start with a letter, arguments are at most four strings or whole numbers, and the target can never be Sluice itself.

## An external fact (version 2)

```
create_rule_with_fact(rule_id, target, method, args, must_true, must_false, fresh_field, max_age, min_field, min_value,
                      fact_url, fact_path, fact_value, fact_max_age)
observe(rule_id)     anyone: the validators read the page and agree; the answer and the time are stored
```

```js
// the monitor must be covered, AND npm's registry page for left-pad 1.3.0 must say version "1.3.0", observed within the last day
await client.writeContract({ address: SLUICE, functionName: "create_rule_with_fact", args: [
  "weth-and-registry", BALLAST, "get_latest", JSON.stringify(["weth-eth"]), "exists,covered", "disputed", "at", 86400n, "", 0n,
  "https://registry.npmjs.org/left-pad/1.3.0", "version", "1.3.0", 86400n,
] });
await anyone.writeContract({ address: SLUICE, functionName: "observe", args: ["weth-and-registry"] });   // validators read the page and agree
```

- The fact is one field of a public JSON page: `fact_path` is dot-separated names or list positions, and the field must read **exactly** as `fact_value` (a string as it is, `true`, `false`, `null`, a number as JSON writes it). The url must be https, at most 300 characters, a public domain name, with no credentials or port. `fact_max_age` is 60 seconds to 30 days. All of it is part of the rule's hash, so `check_pinned` pins the fact too.
- **The validator vote.** The leader fetches the page and proposes whether the field reads as the rule says; every other validator fetches it again on its own and accepts only the same answer. A leader that says "met" for a page that does not say so is voted down, and a page nobody can read records nothing. This is the one nondeterministic step in the contract. `observe` only stores a boolean and a time: `check` and `check_pinned` (views) and `record` contain no web request or nondeterministic block, so a consumer reading Sluice's finalized state sees a deterministic verdict.
- `check` is open only while the on-chain conditions hold **and** the stored observation exists, is not later than the caller's clock, is no older than `fact_max_age` on the caller's clock, and is met. The target is judged first, so an unreadable or failing target closes the rule whatever the page says.
- `observe` can be called again, at most once every 5 minutes per rule, so a rule follows the page in both directions: a later observation of a changed page opens or closes it.
- The observation is stored state like any other, so a consumer that reads Sluice in finalized state sees it about 30 minutes after `observe` on Bradbury.
- What it cannot do: tell whether the page is true. It proves what independent validators read at an address and that they agreed.

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
| Direct | `python -m pytest tests/direct/ -v` | 147 in-memory tests with a fake target that records the address, method, arguments and storage state it was asked for, and (`test_sluice_fact.py`) the fact terms, the page matching rules, the observation cooldown, the open and closed verdicts over time, and the validator vote with forged and malformed leader results |
| Live, v1 | `SLUICE=0x... node scripts/e2e.mjs` | the Bradbury run described below, against the real Ballast contract |
| Live, v2 | `SLUICE=0x... node scripts/e2e-fact.mjs` | the Bradbury run of the external fact described below, against a real npm page and a page in this repository |
| Live, v2 on Studionet | `NETWORK=studionet SLUICE=0x... node scripts/e2e-fact-studio.mjs` | the validator flow on Studionet described below (no `check` over a contract target there) |
| Mutation | `python scripts/mutate.py` | breaks the contract 151 ways, one at a time, and checks a test notices |

Mutation result: 150 of 151 breakages are caught. The one survivor is equivalent: dropping the check that a page size is at least one changes nothing, because a page of zero ids is empty either way. (The 151 include 85 on the fact: its terms, the url rules, the page matching, the validator vote, `observe` and the new verdicts in `check`. A first pass left several url and page-matching mutants alive; each was closed with a test that refuses it for its own reason.)

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

<!--FACT_START-->
## Live evidence, version 2 (Bradbury, run `f1`, 2026-10-23)

Sluice v2 `0xB914771C816E5c25d8757E3877244b4B501D6Ff5`, reading the real deployed Ballast contract `0xd1590EBBA44Cd2d00e003053811E47b9d2209668` (monitor `e2e-v2-covered`, covered and fresh, finalized state only) and, as the external fact, real pages: npm's registry page for left-pad 1.3.0 and a JSON page in this repository (`docs/fact-demo.json`) that the run changes mid-test. Every claim is read back from the chain by `scripts/e2e-fact.mjs`, which fails the run if it is not exactly as expected. Separate wallets declare the rules and observe them. Bradbury finalizes in about 30 minutes, so the steps that read finalized state wait for it.

### The fact must be observed before the rule opens, and a consumer only sees it once it is final

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `create_rule_with_fact ok-f1` | [`0x78a66a7345`](https://explorer-bradbury.genlayer.com/tx/0x78a66a7345e91a6f5ccfbeff031af1caf9f91af770e5a887ea76de3dedeb1ac7) | accepted |
| 2 | `consumer deployed for ok-f1: 0xB4cE9dDF108379877a9AbeD7fF18cDdD34c66E8f` | [`0xf0e18d6bd6`](https://explorer-bradbury.genlayer.com/tx/0xf0e18d6bd613f70dd0d1616407422748bc174acd90f4c9b1921a4859c8dc881a) | deployed at [`0xB4cE9dDF108379877a9AbeD7fF18cDdD34c66E8f`](https://explorer-bradbury.genlayer.com/address/0xB4cE9dDF108379877a9AbeD7fF18cDdD34c66E8f) |
| 3 | `observe` | [`0x12ee0f405c`](https://explorer-bradbury.genlayer.com/tx/0x12ee0f405c1dc77be04ce9da3682929fe1c796089cc458161efec64b173c1aab) | accepted |
| 4 | `consumer.perform` | [`0x99a814daaa`](https://explorer-bradbury.genlayer.com/tx/0x99a814daaa089e53cb9556dd73ab8f32af2e4f6c388f39136876ed9d4bd09194) | accepted |

The rule asks for Ballast's monitor to be covered and fresh **and** for npm's page for left-pad 1.3.0 to say `version` is `1.3.0`. Before any observation `check` was closed with `no_observation`. A consumer contract ([`0xB4cE9dDF108379877a9AbeD7fF18cDdD34c66E8f`](https://explorer-bradbury.genlayer.com/address/0xB4cE9dDF108379877a9AbeD7fF18cDdD34c66E8f)) that reads Sluice's **finalized** state was refused (`Refused: no_observation`), then the validators read the page and agreed, and `check` on the latest state was open (`ok`). The consumer was still refused while that observation was not final, and acted (`performed` 1, note "acted on an open rule with a met fact") only after the observation had FINALIZED, 36 minutes later.

Judged on the caller's own clock: an observation exactly as old as the rule allows (one day) is open (`ok`), one second older is `observation_stale`, and a clock earlier than the observation reads it as `future`.

### An unmet fact keeps the rule closed

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `create_rule_with_fact un-f1` | [`0x0b71b8c99f`](https://explorer-bradbury.genlayer.com/tx/0x0b71b8c99f2a91d67d4f483aeda505d1573bc364c83f2a66ec0a8edc76deee53) | accepted |
| 2 | `observe` | [`0xbb9ce0d30e`](https://explorer-bradbury.genlayer.com/tx/0xbb9ce0d30e517da3e3dec7261711c15d316e0ea61398127c914c23113d57cd11) | accepted |
| 3 | `consumer deployed for un-f1: 0x04194CFddD347e5a2b46acCA8Bf6194546f08F95` | [`0xcb2873fa40`](https://explorer-bradbury.genlayer.com/tx/0xcb2873fa40eab25234337c4c6376a95de74c6862e75ee77d5114f249aadc1447) | deployed at [`0x04194CFddD347e5a2b46acCA8Bf6194546f08F95`](https://explorer-bradbury.genlayer.com/address/0x04194CFddD347e5a2b46acCA8Bf6194546f08F95) |

The same page, for a version it does not show (`9.9.9`). The validators agreed the page does not say that, `check` answered `observation_not_met`, and a consumer reading finalized state ([`0x04194CFddD347e5a2b46acCA8Bf6194546f08F95`](https://explorer-bradbury.genlayer.com/address/0x04194CFddD347e5a2b46acCA8Bf6194546f08F95)) was refused with Sluice's own reason: "Refused: observation_not_met".

### The rule follows the page, in both directions

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `create_rule_with_fact fl-f1` | [`0xae4685fe2a`](https://explorer-bradbury.genlayer.com/tx/0xae4685fe2acb4d5d481d2a011cae0e338bc9cd66ce0410b849890931016aad66) | accepted |
| 2 | `observe while the page says degraded` | [`0x620ef23328`](https://explorer-bradbury.genlayer.com/tx/0x620ef23328996926dc1b626542980dfeb4170177e591d3cba5c2577c863bcf9a) | accepted |
| 3 | `observe (try 1)` | [`0xbac4b5cc56`](https://explorer-bradbury.genlayer.com/tx/0xbac4b5cc56f7bc4d47ebf8e9ea797d4f5bbc25e5ad72e93c125f12009adf5de8) | accepted |
| 4 | `observe (try 1)` | [`0x3b70f4567e`](https://explorer-bradbury.genlayer.com/tx/0x3b70f4567ee0f3180a329b559708fd45ba8c8bd34221f35bba990e8072f64f26) | accepted |

The rule watched the `status` field of `docs/fact-demo.json` for `ok`. While the page said `degraded` the rule was closed (`observation_not_met`). The page was changed to `ok` (a commit to this repository, served by raw.githubusercontent.com) and after the cooldown the next observation opened the rule. The page was then changed back to `degraded` and the next observation closed it again (3 observations in all).

### Refusals

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `create_rule pl-f1 (no fact)` | [`0x18f3a1ab07`](https://explorer-bradbury.genlayer.com/tx/0x18f3a1ab07727b6b762291dabdbadc6592ab21c35c0ed4c66242b7648fab34b5) | accepted |
| 2 | `observe a rule with no fact` | [`0x1afa0467c2`](https://explorer-bradbury.genlayer.com/tx/0x1afa0467c28e72a4799a3f8c8b1acd6e66ccffbabdb29e97801b254f58562abf) | refused by the contract: "This rule has no external fact" |
| 3 | `create_rule_with_fact ur-f1` | [`0xce0ba6f66f`](https://explorer-bradbury.genlayer.com/tx/0xce0ba6f66f9e114d577e521fd4d96e90baf46d9fa5e7238c6e968712a2581948) | accepted |
| 4 | `observe a page that does not exist` | [`0x276e174da1`](https://explorer-bradbury.genlayer.com/tx/0x276e174da191bad26ef6cef6ccc1f4d830e13a85b1d9ef572fcfabb9fefdc303) | refused by the contract: "Page unreadable, nothing recorded. Try again shortly." |

`observe` on a rule with no fact was refused (`This rule has no external fact`). `observe` on a page that returns 404 was refused (`Page unreadable, nothing recorded`) and left the rule as it was: no observation counted, no time stored, and `check` stayed closed with `no_observation`. Bad fact terms were refused before anything was stored: an http url: "url must be https, at most 300 characters"; a fact age under a minute: "fact_max_age must be 60 to 2592000 seconds"; an empty value: "value must be 1 to 200 characters".

### About the run itself

Two parts of this run did not go smoothly, and neither was the contract. The first attempt stopped after the flip and refusals scenarios, because the chain's consensus contract reverted the transactions that deploy the two consumer contracts (a contract deployment, not a Sluice call; deploying the same consumer by hand succeeded straight away). The script now retries a deployment, and the run was resumed from its own log: rules and observations already on chain were not sent again. The resumed run needed more than one attempt for the consumer deployments (each attempt that failed is in the log). Then, after the consumer's accepted `perform`, the script read the consumer's state back before the node had caught up with the accepted write and saw `performed` 0; it now polls that read-back, and the final read showed `performed` 1, as the accepted call had set it. The full event log, with every transaction hash and every failed attempt, is `scripts/e2e-fact-log-f1.json`.
<!--FACT_END-->

<!--STUDIO_START-->
## Live evidence, version 2 on Studionet (run `s3`, 2026-10-24)

Sluice v2 `0xDC5e08F20461D6d0158eb7E58Ea45965A02d1F25` on Studionet, the same source as the Bradbury deployment (`scripts/verify-deployed.mjs` checks both). This run exercises the validator flow, which is the part of v2 that is new: the validators fetch a real public page (npm's registry page for left-pad 1.3.0) and must agree, and the agreed answer and its time are stored. Each rule is read back with `get_rule`. The finalized-read half of the contract (`check` reading the target in finalized state, and a consumer refused until an observation is final) is not run here, because a contract-to-contract read of finalized state never returns on Studionet; it is evidenced on Bradbury above.

### The validators agree a fact is met, and agree it is not

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `create_rule_with_fact met-s3` | [`0x1cb5c59e82`](https://explorer-studio.genlayer.com/tx/0x1cb5c59e826edbd20cec3c0f24032e57e53ff6deff42c01b0bc8a393e7a0dd49) | accepted |
| 2 | `observe` | [`0x07bc186008`](https://explorer-studio.genlayer.com/tx/0x07bc1860087ac5c701fb416a6904f8e8adcb0ef7b6ccfd3c08411d961643025f) | accepted |

The rule asks for `version` to read `1.3.0` on npm's page. After `observe` the rule reads back `met` true, `observations` 1, `observed_at` 1792867196.

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `create_rule_with_fact un-s3` | [`0xeb517b3079`](https://explorer-studio.genlayer.com/tx/0xeb517b307931364263ead16b7bd27063c0cd62a86e574831810799570870bb79) | accepted |
| 2 | `observe` | [`0x76a88723ea`](https://explorer-studio.genlayer.com/tx/0x76a88723ead13e01d6f1b9b6290b77b28d2718617096093da08efe822a8c7e41) | accepted |
| 3 | `observe again at once` | [`0x107f7d3257`](https://explorer-studio.genlayer.com/tx/0x107f7d32577674fa5d6c14b29a3d5621f6f4f2cdd42a6cdb7074aadbc40083e9) | refused by the contract: "Observed too recently, try again later" |

The same page for `9.9.9`: the validators agreed it does not say that (`met` false, `observations` 1), and a second `observe` at once was refused (`Observed too recently`) without recording anything.

### Refusals

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `create_rule_with_fact ur-s3` | [`0x9bf944ce20`](https://explorer-studio.genlayer.com/tx/0x9bf944ce201045da33e5a7e58449174d65836d0699f69569b9da17266029a8b3) | accepted |
| 2 | `observe a page that does not exist` | [`0x4d7138961e`](https://explorer-studio.genlayer.com/tx/0x4d7138961e76e20a68c421b2a32bdd7d27bf3a12baf10ac69dd4732c914db329) | refused by the contract: "Page unreadable, nothing recorded. Try again shortly." |

A page that returns 404: `observe` was refused and nothing was recorded (`observations` 0, `observed_at` 0).

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `create_rule pl-s3 (no fact)` | [`0x9d4e694eef`](https://explorer-studio.genlayer.com/tx/0x9d4e694eef18d2cae7dfb0ab2412c97d9a416f25611eebefef23258227ee3d5f) | accepted |
| 2 | `observe a rule with no fact` | [`0x992bea9a4a`](https://explorer-studio.genlayer.com/tx/0x992bea9a4a6c067cb354b4c8a5c685c168bae195fd1fa4901ad8bd005ddc734c) | refused by the contract: "This rule has no external fact" |

`observe` on a rule with no fact was refused.

### About the run itself

The first attempt of this run stopped part way: the observation of the page that returns 404 sat without settling and the script kept waiting, so it was stopped by hand. A short re-run of the last two scenarios followed while the script was fixed to save its log as it goes, and then the complete run above. The rules of the stopped attempts remain on the contract and are not part of the evidence. The full event log, with every transaction hash, is `scripts/e2e-fact-studio-log-s3.json`.
<!--STUDIO_END-->

## Layout

- `contracts/sluice.py`, `contracts/test_support/sluice_consumer.py`
- `tests/direct/test_sluice.py`, `tests/direct/test_sluice_fact.py`
- `scripts/deploy.mjs`, `scripts/e2e.mjs` (the v1 live run), `scripts/e2e-fact.mjs` (the v2 live run on Bradbury), `scripts/e2e-fact-studio.mjs` (the v2 validator flow on Studionet), `scripts/verify-deployed.mjs`, `scripts/mutate.py`
- `docs/ARCHITECTURE.md`
- `docs/fact-demo.json` is a page this repo controls, used by the live run to show a rule opening and closing as a page changes

## License

MIT
