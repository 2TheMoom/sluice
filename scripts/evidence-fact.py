"""Turns scripts/e2e-fact-log-<RUN>.json into the README's version 2 live-evidence section.
    python scripts/evidence-fact.py f1 > scripts/_evidence-fact.md
"""

import json
import sys

run = sys.argv[1] if len(sys.argv) > 1 else "f1"
log = json.load(open(f"scripts/e2e-fact-log-{run}.json", encoding="utf-8"))
EX = "https://explorer-bradbury.genlayer.com/tx/"
AD = "https://explorer-bradbury.genlayer.com/address/"
ev = log["events"]
res = log["results"]


def link(h):
    return f"[`{h[:12]}`]({EX}{h})"


def addr(a):
    return f"[`{a}`]({AD}{a})"


def rows(scn, skip=()):
    out = ["| # | Call | Transaction | Result |", "|---|---|---|---|"]
    n = 0
    seen = set()
    for e in ev:
        if e["scn"] != scn or not e.get("hash"):
            continue
        if e["hash"] in seen or any(s in e["msg"] for s in skip):
            continue
        seen.add(e["hash"])
        msg = e["msg"]
        label = msg.split(" " + e["hash"][:12])[0]
        if "consumer deployed" in msg:
            out.append(f"| {n + 1} | `{label}` | {link(e['hash'])} | deployed at {addr(e['address'])} |")
        elif e.get("refused"):
            out.append(f"| {n + 1} | `{label}` | {link(e['hash'])} | refused by the contract: \"{e['reason']}\" |")
        elif e.get("fn") and e.get("exec") == "FINISHED_WITH_RETURN":
            out.append(f"| {n + 1} | `{label}` | {link(e['hash'])} | accepted |")
        else:
            continue
        n += 1
    return out


def lines_of(scn, *needles):
    return [e["msg"] for e in ev if e["scn"] == scn and any(k in e["msg"] for k in needles)]


L = [f"## Live evidence, version 2 (Bradbury, run `{run}`, {log['started'][:10]})", ""]
L.append(
    f"Sluice v2 `{log['sluice']}`, reading the real deployed Ballast contract `{log['ballast']}` (monitor `{log['monitor']}`, covered and fresh, finalized state only) "
    "and, as the external fact, real pages: npm's registry page for left-pad 1.3.0 and a JSON page in this repository (`docs/fact-demo.json`) that the run changes mid-test. "
    "Every claim is read back from the chain by `scripts/e2e-fact.mjs`, which fails the run if it is not exactly as expected. "
    "Separate wallets declare the rules and observe them. Bradbury finalizes in about 30 minutes, so the steps that read finalized state wait for it."
)

L += ["", "### The fact must be observed before the rule opens, and a consumer only sees it once it is final", ""]
L += rows("ok")
r = res["ok"]
L += [
    "",
    "The rule asks for Ballast's monitor to be covered and fresh **and** for npm's page for left-pad 1.3.0 to say `version` is `1.3.0`. "
    "Before any observation `check` was closed with `no_observation`. "
    f"A consumer contract ({addr(r['consumer'])}) that reads Sluice's **finalized** state was refused (`Refused: no_observation`), "
    "then the validators read the page and agreed, and `check` on the latest state was open (`ok`). The consumer was still refused while that observation was not final, "
    "and acted (`performed` 1, note \"acted on an open rule with a met fact\") only after the observation had FINALIZED, 36 minutes later.",
    "",
    f"Judged on the caller's own clock: an observation exactly as old as the rule allows (one day) is open (`{r['edge']}`), one second older is `{r['late']}`, and a clock earlier than the observation reads it as `{r['early']}`.",
]

L += ["", "### An unmet fact keeps the rule closed", ""]
L += rows("unmet")
r = res["unmet"]
L += [
    "",
    "The same page, for a version it does not show (`9.9.9`). The validators agreed the page does not say that, `check` answered `observation_not_met`, and a consumer reading finalized state "
    f"({addr(r['consumer'])}) was refused with Sluice's own reason: \"{r['refusal'].strip()}\".",
]

L += ["", "### The rule follows the page, in both directions", ""]
L += rows("flip")
r = res["flip"]
L += [
    "",
    "The rule watched the `status` field of `docs/fact-demo.json` for `ok`. While the page said `degraded` the rule was closed (`observation_not_met`). The page was changed to `ok` (a commit to this repository, "
    "served by raw.githubusercontent.com) and after the cooldown the next observation opened the rule. The page was then changed back to `degraded` and the next observation closed it again "
    f"({r['observations']} observations in all).",
]

L += ["", "### Refusals", ""]
L += rows("refusals")
r = res["refusals"]
L += [
    "",
    "`observe` on a rule with no fact was refused (`This rule has no external fact`). `observe` on a page that returns 404 was refused (`Page unreadable, nothing recorded`) and left the rule as it was: "
    "no observation counted, no time stored, and `check` stayed closed with `no_observation`. Bad fact terms were refused before anything was stored: "
    + "; ".join(f"{b['label']}: \"{b['reason']}\"" for b in r["bad_terms"])
    + ".",
    "",
    "### About the run itself",
    "",
    "Two parts of this run did not go smoothly, and neither was the contract. The first attempt stopped after the flip and refusals scenarios, because the chain's consensus contract reverted the transactions that deploy the "
    "two consumer contracts (a contract deployment, not a Sluice call; deploying the same consumer by hand succeeded straight away). The script now retries a deployment, and the run was resumed from its own log: rules and "
    "observations already on chain were not sent again. The resumed run needed more than one attempt for the consumer deployments (each attempt that failed is in the log). Then, after the consumer's accepted `perform`, "
    "the script read the consumer's state back before the node had caught up with the accepted write and saw `performed` 0; it now polls that read-back, and the final read showed `performed` 1, as the accepted call had set it. "
    f"The full event log, with every transaction hash and every failed attempt, is `scripts/e2e-fact-log-{run}.json`.",
]
print("\n".join(L))
