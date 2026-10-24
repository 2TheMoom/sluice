"""Turns scripts/e2e-fact-studio-log-<RUN>.json into the README's Studionet evidence section.
    python scripts/evidence-fact-studio.py s3 > scripts/_evidence-studio.md
"""

import json
import sys

run = sys.argv[1] if len(sys.argv) > 1 else "s3"
log = json.load(open(f"scripts/e2e-fact-studio-log-{run}.json", encoding="utf-8"))
EX = "https://explorer-studio.genlayer.com/tx/"
ev = log["events"]
res = log["results"]


def link(h):
    return f"[`{h[:12]}`]({EX}{h})"


def rows(scn):
    out = ["| # | Call | Transaction | Result |", "|---|---|---|---|"]
    n = 0
    for e in ev:
        if e["scn"] != scn or not e.get("hash") or not e.get("fn"):
            continue
        label = e["msg"].split(" " + e["hash"][:12])[0]
        n += 1
        result = f"refused by the contract: \"{e['reason']}\"" if e.get("refused") else "accepted"
        out.append(f"| {n} | `{label}` | {link(e['hash'])} | {result} |")
    return out


L = [f"## Live evidence, version 2 on Studionet (run `{run}`, {log['started'][:10]})", ""]
L.append(
    f"Sluice v2 `{log['sluice']}` on Studionet, the same source as the Bradbury deployment (`scripts/verify-deployed.mjs` checks both). "
    "This run exercises the validator flow, which is the part of v2 that is new: the validators fetch a real public page (npm's registry page for left-pad 1.3.0) and must agree, "
    "and the agreed answer and its time are stored. Each rule is read back with `get_rule`. The finalized-read half of the contract (`check` reading the target in finalized state, and a consumer "
    "refused until an observation is final) is not run here, because a contract-to-contract read of finalized state never returns on Studionet; it is evidenced on Bradbury above."
)
L += ["", "### The validators agree a fact is met, and agree it is not", ""]
L += rows("met")
f = res["met"]["fact"]
L += [
    "",
    f"The rule asks for `version` to read `1.3.0` on npm's page. After `observe` the rule reads back `met` {str(f['met']).lower()}, `observations` {f['observations']}, `observed_at` {f['observed_at']}.",
    "",
]
L += rows("unmet")
f = res["unmet"]["fact"]
L += [
    "",
    f"The same page for `9.9.9`: the validators agreed it does not say that (`met` {str(f['met']).lower()}, `observations` {f['observations']}), and a second `observe` at once was refused (`Observed too recently`) without recording anything.",
]
L += ["", "### Refusals", ""]
L += rows("unreadable")
f = res["unreadable"]["fact"]
L += [
    "",
    f"A page that returns 404: `observe` was refused and nothing was recorded (`observations` {f['observations']}, `observed_at` {f['observed_at']}).",
    "",
]
L += rows("plain")
L += ["", "`observe` on a rule with no fact was refused.", "", "### About the run itself", ""]
L += [
    "The first attempt of this run stopped part way: the observation of the page that returns 404 sat without settling and the script kept waiting, so it was stopped by hand. A short re-run of the last two scenarios followed while the script was fixed to save its log as it goes, and then the complete run above. "
    "The rules of the stopped attempts remain on the contract and are not part of the evidence. "
    f"The full event log, with every transaction hash, is `scripts/e2e-fact-studio-log-{run}.json`.",
]
print("\n".join(L))
