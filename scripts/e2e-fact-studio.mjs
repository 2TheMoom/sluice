// Live run of Sluice v2's validator flow (create_rule_with_fact, observe) on Studionet, the network the portal's Explorer lists.
//   NETWORK=studionet SLUICE=0x.. RUN=s1 node scripts/e2e-fact-studio.mjs        writes scripts/e2e-fact-studio-log-<RUN>.json
//
// What runs here and what does not. The observe flow (the validators fetch a public page and agree) runs in full. `check` does not: it reads the rule's
// target contract in FINALIZED state through a contract-to-contract call, and on Studionet such a call never returns (see the probe in the Lading repo,
// docs/studionet-nested-final-read-probe.py). So this run reads each rule back with get_rule instead, and the finalized-read half of the contract
// (consumers refused until an observation is final, then acting) is evidenced on Bradbury by scripts/e2e-fact.mjs.
//
// Scenarios, each its own rule over the real Ballast on Studionet (the target is only stored here, never read):
//   met        npm's registry page for left-pad 1.3.0 must say version "1.3.0": the validators agree it is met, the answer and the time are stored
//   unmet      the same page for "9.9.9": the validators agree it is not met
//   unreadable a page that does not exist: observe is refused and records nothing, nothing starts the cooldown
//   cooldown   a second observe at once is refused
//   plain      observe on a rule with no fact is refused
// Wallets (kept outside the repo): gl-test-1 declares the rules, gl-test-2 observes.
import { writeFileSync } from "fs";
import { WALLETS, clientFor, plain, read, refusalOf, send, sleep, ts } from "./lib.mjs";
import { NETWORK, chain } from "./network.mjs";

const S = process.env.SLUICE;
const RUN = process.env.RUN || Date.now().toString(36);
if (!S) throw new Error("set SLUICE=<address>");
if (!chain.isStudio) throw new Error("this run is for Studionet (NETWORK=studionet)");
const BALLAST = "0x4477105a01b7ddbCc449837Ec8Ed9900EbFD2a08";
const MONITOR = "e2e-studio2-covered";
const NPM = "https://registry.npmjs.org/left-pad/1.3.0";
const MISSING = "https://registry.npmjs.org/left-pad/9.9.9-no-such-version";
const DAY = 86400n;
const deployer = clientFor("gl-test-1");
const caller = clientFor("gl-test-2");
const id = (k) => `${k}-${RUN}`;

const LOGFILE = `scripts/e2e-fact-studio-log-${RUN}.json`;
const LOG = { run: RUN, network: NETWORK, sluice: S, started: new Date().toISOString(), events: [], results: {} };
const log = (scn, msg, extra) => {
  console.log(`[${ts()}] ${scn.padEnd(10)} ${msg}`);
  LOG.events.push({ t: new Date().toISOString(), scn, msg, ...(extra || {}) });
  writeFileSync(LOGFILE, JSON.stringify(plain(LOG), null, 1)); // saved as it goes, so a run that is stopped keeps what it did
};
const ok = (r) => (r.status === "ACCEPTED" || r.status === "FINALIZED") && r.exec === "FINISHED_WITH_RETURN";

async function until(scn, label, fn, ms = 300000) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() - t0 > ms) throw new Error(`${scn}: ${label} did not come true`);
    await sleep(3000);
  }
}
async function accepted(scn, client, fn, args, label) {
  for (let a = 1; a <= 5; a++) {
    const r = await send(client, S, fn, args);
    log(scn, `${label || fn} ${r.hash.slice(0, 12)} ${r.status} exec=${r.exec}`, { fn, hash: r.hash, status: r.status, exec: r.exec });
    if (ok(r)) return r;
    await sleep(10000);
  }
  throw new Error(`${scn}: ${label || fn} was never accepted`);
}
async function refused(scn, client, fn, args, text, label) {
  const r = await send(client, S, fn, args);
  const reason = refusalOf(r.tx) ?? `${r.status} ${r.exec}`;
  log(scn, `${label || fn} ${r.hash.slice(0, 12)} refused: "${reason}"`, { fn, hash: r.hash, refused: true, reason });
  if (ok(r)) throw new Error(`${scn}: ${fn} should have been refused`);
  if (text && !reason.includes(text)) throw new Error(`${scn}: ${fn} refused for "${reason}", expected "${text}"`);
}
const getRule = (rid) => read(S, "get_rule", [rid]);
const declare = (scn, rid, url, path, value) =>
  accepted(scn, deployer, "create_rule_with_fact", [rid, BALLAST, "get_latest", JSON.stringify([MONITOR]), "exists,covered", "disputed", "at", 30n * DAY, "", 0n, url, path, value, DAY], `create_rule_with_fact ${rid}`);

async function observeAndRead(scn, rid, label) {
  const before = Number((await getRule(rid)).fact.observations);
  await accepted(scn, caller, "observe", [rid], label);
  return until(scn, "the observation to read back", async () => (Number((await getRule(rid)).fact.observations) > before) && (await getRule(rid)));
}

async function met() {
  const scn = "met", rid = id("met");
  await declare(scn, rid, NPM, "version", "1.3.0");
  await until(scn, "the rule to read back", async () => (await getRule(rid)).fact.url === NPM);
  const r0 = (await getRule(rid)).fact;
  if (r0.observations !== 0) throw new Error(`${scn}: a new rule has no observation`);
  const r = await observeAndRead(scn, rid, "observe");
  if (r.fact.met !== true || r.fact.observations !== 1 || !(r.fact.observed_at > 0)) throw new Error(`${scn}: expected a met observation, got ${JSON.stringify(r.fact)}`);
  log(scn, `the validators read the page and agreed: version is 1.3.0 (met=${r.fact.met}, stored at ${r.fact.observed_at})`);
  LOG.results.met = { id: rid, fact: r.fact };
}
async function unmet() {
  const scn = "unmet", rid = id("un");
  await declare(scn, rid, NPM, "version", "9.9.9");
  await until(scn, "the rule to read back", async () => (await getRule(rid)).fact.url === NPM);
  const r = await observeAndRead(scn, rid, "observe");
  if (r.fact.met !== false || r.fact.observations !== 1) throw new Error(`${scn}: expected an unmet observation, got ${JSON.stringify(r.fact)}`);
  log(scn, `the validators agreed the page does not say 9.9.9 (met=${r.fact.met}, observation stored)`);
  await refused(scn, caller, "observe", [rid], "too recently", "observe again at once");
  const r2 = (await getRule(rid)).fact;
  if (r2.observations !== 1) throw new Error(`${scn}: a refused observe must record nothing`);
  LOG.results.unmet = { id: rid, fact: r2, cooldown: "a second observe at once was refused" };
}
async function unreadable() {
  const scn = "unreadable", rid = id("ur");
  await declare(scn, rid, MISSING, "version", "1.3.0");
  await until(scn, "the rule to read back", async () => (await getRule(rid)).fact.url === MISSING);
  await refused(scn, caller, "observe", [rid], "unreadable", "observe a page that does not exist");
  const f = (await getRule(rid)).fact;
  if (f.observations !== 0 || f.observed_at !== 0) throw new Error(`${scn}: an unreadable page must record nothing`);
  log(scn, "an unreadable page records nothing: observations 0, no time stored");
  LOG.results.unreadable = { id: rid, fact: f };
}
async function plainRule() {
  const scn = "plain", rid = id("pl");
  await accepted(scn, deployer, "create_rule", [rid, BALLAST, "get_latest", JSON.stringify([MONITOR]), "exists,covered", "disputed", "at", 30n * DAY, "", 0n], `create_rule ${rid} (no fact)`);
  await until(scn, "the rule to read back", async () => (await getRule(rid)).id === rid);
  await refused(scn, caller, "observe", [rid], "no external fact", "observe a rule with no fact");
  LOG.results.plain = { id: rid };
}

log("setup", `sluice ${S} on ${NETWORK}`);
const every = { met, unmet, unreadable, plain: plainRule };
const all = Object.fromEntries(Object.entries(every).filter(([k]) => !process.env.ONLY || process.env.ONLY.split(",").includes(k)));
// one after another: the scenarios share two wallets, and two writes from one wallet at once would collide on the nonce
const settled = [];
for (const [name, f] of Object.entries(all)) {
  try {
    await f();
    settled.push({ status: "fulfilled" });
  } catch (e) {
    settled.push({ status: "rejected", reason: e });
    log(name, `FAILED: ${String(e?.message || e).slice(0, 600)}`);
  }
}
LOG.finished = new Date().toISOString();
writeFileSync(LOGFILE, JSON.stringify(plain(LOG), null, 1));
console.log("\nRESULTS", JSON.stringify(plain(LOG.results), null, 1));
process.exitCode = settled.some((s) => s.status === "rejected") ? 1 : 0;
