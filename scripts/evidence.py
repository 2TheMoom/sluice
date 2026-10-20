"""Turns scripts/e2e-log-<RUN>.json into the README's live-evidence section.
    python scripts/evidence.py v1 > scripts/_evidence.md
"""

import json
import re
import sys

run = sys.argv[1] if len(sys.argv) > 1 else "v1"
log = json.load(open(f"scripts/e2e-log-{run}.json", encoding="utf-8"))
EX = "https://explorer-bradbury.genlayer.com/tx/"
ev = log["events"]


def tx_for(fn, rid):
    for e in ev:
        if e.get("fn") == fn and e["msg"].startswith(f"{fn} {rid} ") and e.get("status") in ("ACCEPTED", "FINALIZED"):
            return e["hash"]
    return None


def link(h):
    return f"[`{h[:12]}`]({EX}{h})" if h else ""


WHAT = {
    "covered-30d": "monitor `e2e-v2-covered` exists, is covered, is not disputed, and was attested within 30 days",
    "covered-1h": "the same monitor, but the attestation must be under one hour old",
    "strict": "monitor `e2e-v2-strict` (a 101% floor, attested short) must be covered",
    "floor-ok": "monitor `e2e-v2-twochains` is covered and its ratio is at least 11,000 bps",
    "floor-high": "the same monitor with a floor of 12,000 bps",
    "no-monitor": "a monitor id that does not exist",
    "real-wallet": "monitor `mm-weth-base-1` (the real-MetaMask run) is covered, fresh within a day, ratio at least 10,000 bps",
}
lines = [f"## Live evidence (Bradbury, run `{run}`, {log['started'][:10]})", ""]
lines.append(
    f"Sluice `{log['sluice']}`, reading the real, deployed Ballast contract `{log['ballast']}` (finalized state only). "
    "Seven rules over real monitors, each answered by Sluice with the caller's own clock, then recorded on chain, and the recorded verdict read back and compared. "
    "The run fails if any answer is not exactly the expected one."
)
lines += ["", "| Rule | What it asks of Ballast | Sluice's answer | Created | Recorded |", "|---|---|---|---|---|"]
for rid, r in log["rules"].items():
    v = r["verdict"]
    ans = ("**open**" if v["open"] else "closed") + f", `{v['code']}`" + (f" on `{v['field']}`" if v["field"] else "") + (f", age {v['age']:,} s" if v["age"] >= 0 else "")
    lines.append(f"| `{rid}` | {WHAT[rid]} | {ans} | {link(tx_for('create_rule', rid))} | {link(tx_for('record', rid))} |")
lines += [
    "",
    "Things the table shows: the same finalized data is open under a 30 day limit and closed under a one hour limit (the age is judged on the caller's clock), "
    "a short monitor is refused with the field that failed, a floor is inclusive and reported as `below_min`, and a monitor that cannot be read closes the gate instead of opening it. "
    "Ballast raises \"Monitor not found\" for an unknown id, and Sluice reports exactly that as `unreadable`.",
    "",
    "Bad calls were refused with the contract's own reason: a rule hash that is not the expected one, a negative clock, and an unknown rule. A pinned check with the right hash was open.",
    "",
    "### A contract reading the gate in finalized state",
    "",
    "A nested read: a consumer contract asks Sluice (finalized state), and Sluice reads Ballast (finalized state). It ran only after the rules had FINALIZED, which took about 26 minutes.",
    "",
]
c = log["consumers"]
lines += [
    f"- Open rule `covered-30d`: consumer [`{c['open']['address']}`](https://explorer-bradbury.genlayer.com/address/{c['open']['address']}) acted ({link(c['open']['act'])}), `performed = 1`.",
    f"- Closed rule `strict`: consumer [`{c['closed']['address']}`](https://explorer-bradbury.genlayer.com/address/{c['closed']['address']}) was refused with Sluice's own reason, `Refused: not_true covered` ({link(c['closed']['refused'])}).",
    "",
    "Both consumers are pinned to the rule's hash, so a lookalike rule id could not stand in for the audited one.",
    "",
    "A note on honesty about the run itself: the first attempt stopped after the fourth `record` because the script read the rule back before the node had caught up with the accepted write, so it saw `records = 0`. That is a script timing error, not a contract one. The script now polls the read-back and can resume from its own log, and the resumed run is the one above (the full event log, with every transaction hash, is `scripts/e2e-log-" + run + ".json`).",
]
print("\n".join(lines))
