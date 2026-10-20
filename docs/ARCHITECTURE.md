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

## What it cannot do

It cannot tell you the target is honest, only that its finalized answer meets the rule. It reads one view call per rule. It cannot make an answer fresher than the target's own last write. And because it reads finalized state, a new fact takes about 30 minutes on Bradbury to become visible to it.
