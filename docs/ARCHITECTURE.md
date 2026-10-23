# Sluice: design and threat model

Sluice is a gate any contract can ask a question: "is this other contract's answer final, fresh and in the shape I need?" You declare a rule once (which contract, which view method, which arguments, which fields must be true, false, fresh or above a floor). Sluice then answers `open` or `closed, because ...` from that contract's **finalized** state only.

It exists because the same four mistakes keep being made by contracts that read other contracts on GenLayer, and every one of them cost us a bug:

1. reading accepted state, which an appeal can still reverse, instead of finalized state;
2. taking an answer on faith: a field that is `1` instead of `true`, a string where a number belongs, a missing key;
3. judging freshness on the other contract's clock instead of your own;
4. treating "could not read it" as a yes, or as a no that triggers a refund.

## Shape

```
create_rule   anyone, once per id. target, view method, arguments, and the conditions. Nothing can be changed afterwards.
check         view. check(rule_id, now) reads the target in FINALIZED state and returns {open, code, field, message, age}.
check_pinned  view. The same, but refuses unless the rule still has the hash the caller expects.
record        write. Evaluates the rule on the transaction's own clock and stores the verdict, so it is visible on an explorer.
```

```
create_rule_with_fact   create_rule, plus one fact about the world fixed now: a field of a public JSON page that must read exactly as a value, and how old an observation may be.
observe                 anyone. The validators fetch the page and must agree on whether the field reads as the rule says. The agreed answer and the time are stored. A page that cannot be read records nothing.
```

`check`, `check_pinned` and `record` never read the world. The only nondeterministic step in the contract is the page read inside `observe`, which stores a boolean and a timestamp; a rule with a fact is open only while that stored observation is met and fresh on the caller's clock. So a consumer that reads Sluice in finalized state gets a deterministic verdict built from two finalized things: the target's answer and the validators' agreed observation.

## The validated page read

`observe` runs one nondeterministic block (`gl.vm.run_nondet_unsafe`). The leader fetches the page and proposes `{kind, met}`: `kind` is `read` or `unreadable`, `met` is whether the field at the path reads exactly as the rule's value. Every other validator fetches the page on its own and votes to accept only if its own `kind` and `met` are identical. A leader that claims "met" for a page that does not say so is voted down, and so is one that claims "unreadable" for a page the validators can read. An unreadable page (an error status, an empty body, a page over 2,000,000 bytes, a failed request) records nothing and the call is refused.

Conditions a rule can hold: fields that must be `true`, fields that must be `false`, one timestamp field that must be no older than `max_age`, and one integer field that must be at least `min_value`. At least one condition is required, so a rule can never be a gate that is always open.

## Threats considered

| # | Threat | Defense | Where it is tested |
|---|---|---|---|
| 1 | Reading state that can still be appealed away | Every read of the target is `LATEST_FINAL`. The test records which state was asked for | state tests |
| 2 | A wrong-shaped answer taken as true | A boolean field must be a real boolean. Timestamps and floors must be real integers (a boolean is refused, a string is refused). A missing field or a non-object answer closes the gate | shape tests |
| 3 | Freshness judged on the target's clock | Age is `now - timestamp` where `now` is supplied by the caller, who uses their own clock. A timestamp in the future closes the gate | freshness tests |
| 4 | An unreadable target counted as open | Any error reading the target closes the gate with code `unreadable`. The gate is fail-closed | unreadable tests |
| 5 | A rule that changes under a consumer | Rules are immutable and have no owner. `check_pinned` refuses a rule whose hash is not the one expected, so a consumer can also defend against a lookalike id | immutability and pin tests |
| 6 | A rule that is open for everything | A rule with no condition is refused. Fresh and max age come together. The true and false lists cannot overlap | rule validation tests |
| 7 | Hostile method names or arguments | The method must be a plain lowercase identifier, arguments are at most four strings or whole numbers, field names are plain identifiers. The target can never be Sluice itself | validation tests |
| 8 | Id squatting | First come, first served, so consumers should pin the hash, not trust the name | pin tests |
| 9 | A caller lying about the time | Only affects the caller's own answer. A consumer contract must pass its own clock | documented |
| 10 | The gate answering from a target that is not finalized yet | GenVM aborts the caller itself in that case (`invalid_contract absent_runner_comment`). Python cannot catch it. It is a limit of the platform, documented and seen live | README, live run |
| 11 | A leader lies about the page ("met" when it is not) | The validators re-fetch the page themselves and accept only the same `kind` and `met`; forged, altered and malformed leader results are voted down | validator-vote tests (forged fields, wrong shapes, a crashed leader), live run |
| 12 | The page cannot be read, or is huge | An error status, an empty body, an oversized body or a failed request is `unreadable`: the call is refused, nothing is recorded, the cooldown does not start, and the rule stays closed (`no_observation`) | unreadable tests, live run |
| 13 | A stale observation keeps a rule open | `check` closes the rule with `observation_stale` once the observation is older than `fact_max_age` on the caller's clock, and with `future` when it is later than the caller's clock | freshness tests, live run |
| 14 | The page changes after it was observed | The rule follows the next observation in both directions (it can open and close again), and no observation is valid for longer than `fact_max_age` | flip tests, live flip scenario |
| 15 | The target is bad but the page is fine (or the reverse) | The target is judged first: an unreadable or failing target closes the rule whatever the page says. The fact can only close a rule that the target would open, never open one it would close | precedence tests |
| 16 | A hostile url (credentials, a port, an internal host, a one-label host, odd characters) | https only, at most 300 characters, no credentials or port, a public dotted domain name, valid host labels, no whitespace or quote characters | url tests (each rule tested for its own refusal) |
| 17 | Observation spam | One recorded look per rule every 5 minutes | rate-limit tests |
| 18 | A lookalike rule with another fact | The url, path, value and age are part of the rule's hash, so `check_pinned` refuses a rule whose fact differs from the one expected | hash tests |

## What it cannot do

It cannot tell you the target is honest, only that its finalized answer meets the rule. For a rule with a fact it cannot tell whether the page tells the truth: it proves what independent validators read at that address and that they agreed. It reads one view call per rule. It cannot make an answer fresher than the target's own last write. And because it reads finalized state, a new fact takes about 30 minutes on Bradbury to become visible to it.
