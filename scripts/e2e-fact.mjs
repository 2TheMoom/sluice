// Live end to end run of Sluice's external fact (create_rule_with_fact, observe) on Bradbury, with the real deployed Ballast as the on-chain target.
//   SLUICE=0x.. RUN=f1 node scripts/e2e-fact.mjs        writes scripts/e2e-fact-log-<RUN>.json
//
// Every rule watches the same real Ballast monitor (covered, fresh) AND one fact about the world, settled by the validators:
//   ok       npm's registry page for left-pad 1.3.0 must say version "1.3.0". Before any observation the rule is closed (no_observation). A consumer
//            contract that reads Sluice's FINALIZED state is refused while the observation is not final, then acts once it is.
//   unmet    the same page, for a version it does not show ("9.9.9"): the validators agree it is not met, the rule stays closed (observation_not_met),
//            and the consumer is refused with that reason once the observation is final.
//   flip     docs/fact-demo.json in this repo's main branch must say status "ok". While it says "degraded" the rule is closed. After the file is
//            changed and the cooldown has passed, observe opens the rule; changing it back and observing again closes it.
//   stale    an observation older than the rule allows closes it, judged on the caller's own clock (a check with a later "now"), and an observation
//            from the future of the caller's clock is refused.
//   refusals observe on a rule with no fact is refused; a rule whose page does not exist is refused and records nothing.
// Wallets (kept outside the repo): gl-test-1 declares rules and deploys consumers, gl-test-2 observes and calls the consumers.
import { existsSync, readFileSync, writeFileSync } from "fs";
import { clientFor, read, reader, reasonOf, refusalOf, send, sleep, ts } from "./lib.mjs";
import { NETWORK } from "./network.mjs";

const S = process.env.SLUICE;
const RUN = process.env.RUN || Date.now().toString(36);
if (!S) throw new Error("set SLUICE=<address>");
const BALLAST = "0xd1590EBBA44Cd2d00e003053811E47b9d2209668";
const MONITOR = "e2e-v2-covered";
const NPM = "https://registry.npmjs.org/left-pad/1.3.0";
const DEMO = "https://raw.githubusercontent.com/2TheMoom/sluice/main/docs/fact-demo.json";
const MISSING = "https://registry.npmjs.org/left-pad/9.9.9-no-such-version";
const DAY = 86400;
const RECHECK = 300;
const deployer = clientFor("gl-test-1");
const caller = clientFor("gl-test-2");
const id = (k) => `${k}-${RUN}`;

const LOGFILE = `scripts/e2e-fact-log-${RUN}.json`;
// A run that stopped part way (a script or network error, never a contract one) can be resumed with RESUME=1: rules and observations already on chain
// and recorded in the log are not sent again, and what was already final is not waited for twice.
const PREV = process.env.RESUME && existsSync(LOGFILE) ? JSON.parse(readFileSync(LOGFILE, "utf8")) : null;
const LOG = { run: RUN, network: NETWORK, sluice: S, ballast: BALLAST, monitor: MONITOR, started: PREV?.started ?? new Date().toISOString(), events: PREV?.events ?? [], results: PREV?.results ?? {} };
const prevHash = (fn, msgStart) => PREV?.events.find((e) => e.fn === fn && e.msg.startsWith(msgStart) && (e.status === "ACCEPTED" || e.status === "FINALIZED") && e.exec === "FINISHED_WITH_RETURN")?.hash;
const log = (scn, msg, extra) => {
  console.log(`[${ts()}] ${scn.padEnd(9)} ${msg}`);
  LOG.events.push({ t: new Date().toISOString(), scn, msg, ...(extra || {}) });
};
const ok = (r) => (r.status === "ACCEPTED" || r.status === "FINALIZED") && r.exec === "FINISHED_WITH_RETURN";
const now = () => Math.floor(Date.now() / 1000);
const lock = {};
const serial = (who, fn) => (lock[who] = (lock[who] || Promise.resolve()).then(fn, fn));

async function accepted(scn, who, client, address, fn, args, label) {
  return serial(who, async () => {
    for (let a = 1; a <= 6; a++) {
      const r = await send(client, address, fn, args);
      log(scn, `${label || fn} ${r.hash.slice(0, 12)} ${r.status} exec=${r.exec}`, { fn, hash: r.hash, status: r.status, exec: r.exec });
      if (ok(r)) return r;
      await sleep(20000);
    }
    throw new Error(`${scn}: ${label || fn} was never accepted`);
  });
}
/** A write that must be refused: sent for real so it is on chain, and the contract's own reason is read from the node's answer. */
async function refused(scn, who, client, address, fn, args, text, label) {
  return serial(who, async () => {
    let reason = "";
    try {
      await client.simulateWriteContract({ address, functionName: fn, args });
    } catch (e) {
      reason = reasonOf(e);
    }
    const r = await send(client, address, fn, args);
    if (ok(r)) throw new Error(`${scn}: ${fn} should have been refused`);
    reason = refusalOf(r.tx) ?? (reason || `${r.status} ${r.exec}`);
    log(scn, `${label || fn} ${r.hash.slice(0, 12)} refused: "${reason}"`, { fn, hash: r.hash, refused: true, reason });
    if (text && !reason.includes(text)) throw new Error(`${scn}: ${fn} refused for "${reason}", expected "${text}"`);
    return reason;
  });
}
async function until(scn, label, fn, ms = 1800000) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() - t0 > ms) throw new Error(`${scn}: ${label} did not come true`);
    await sleep(10000);
  }
}
async function finalized(scn, hash, label) {
  const t0 = Date.now();
  for (let a = 0; a < 40; a++) {
    try {
      await reader.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 400, interval: 10000 });
      log(scn, `${label} FINALIZED ${((Date.now() - t0) / 60000).toFixed(1)} min after the wait began`);
      return;
    } catch {
      await sleep(15000);
    }
  }
  throw new Error(`${scn}: ${label} did not finalize`);
}
const rule = (scn, rid, url, path, value, age = DAY) => {
  const done = prevHash("create_rule_with_fact", `create_rule_with_fact ${rid} `);
  if (done) {
    log(scn, `create_rule_with_fact ${rid} already on chain (resumed)`);
    return Promise.resolve({ hash: done });
  }
  return ruleNew(scn, rid, url, path, value, age);
};
const ruleNew = (scn, rid, url, path, value, age = DAY) =>
  accepted(scn, "gl-test-1", deployer, S, "create_rule_with_fact", [rid, BALLAST, "get_latest", JSON.stringify([MONITOR]), "exists,covered", "disputed", "at", BigInt(30 * DAY), "", 0n, url, path, value, BigInt(age)], `create_rule_with_fact ${rid}`);
const getRule = (rid) => read(S, "get_rule", [rid]);
const check = (rid, at) => read(S, "check", [rid, at ?? now()]);
async function deployConsumer(scn, rid) {
  const digest = (await getRule(rid)).digest;
  return serial("gl-test-1", async () => {
    const code = new Uint8Array(readFileSync("contracts/test_support/sluice_consumer.py"));
    for (let a = 1; ; a++) {
      try {
        const hash = await deployer.deployContract({ code, args: [S, rid, digest] });
        const rec = await deployer.waitForTransactionReceipt({ hash, status: "ACCEPTED", retries: 200, interval: 5000 });
        const address = rec.txDataDecoded?.contractAddress ?? rec.data?.contract_address;
        log(scn, `consumer deployed for ${rid}: ${address}`, { hash, address });
        return address;
      } catch (e) {
        // the consensus contract sometimes reverts a deployment under load (seen twice in the first run); a deployment is retried, nothing else is
        log(scn, `consumer deployment attempt ${a} failed (${String(e.message || e).slice(0, 90)})${a < 4 ? ", trying again" : ""}`);
        if (a >= 4) throw e;
        await sleep(60000);
      }
    }
  });
}
const observeOk = async (scn, rid, label) => {
  const before = Number((await getRule(rid)).fact.observations);
  const earlier = (label || "observe") === "observe" && before > 0 ? PREV?.events.find((e) => e.scn === scn && e.fn === "observe" && e.exec === "FINISHED_WITH_RETURN" && e.msg.startsWith("observe ")) : null;
  if (earlier) {
    log(scn, `observe ${earlier.hash.slice(0, 12)} already on chain (resumed)`);
    return { r: { hash: earlier.hash }, rule: await getRule(rid) };
  }
  const r = await accepted(scn, "gl-test-2", caller, S, "observe", [rid], label || "observe");
  const rr = await until(scn, "the observation to read back", async () => (Number((await getRule(rid)).fact.observations) > before) && (await getRule(rid)));
  return { r, rule: rr };
};

// ------------------------------------------------------------------------------------------------------------------ scenarios

async function okScenario() {
  const scn = "ok", rid = id("ok");
  const c = await rule(scn, rid, NPM, "version", "1.3.0");
  const v0 = await check(rid);
  if (v0.code !== "no_observation") throw new Error(`${scn}: before any observation the rule must be closed, got ${v0.code}`);
  log(scn, `before any observation: open=${v0.open} code=${v0.code}`);
  await finalized(scn, c.hash, "create_rule_with_fact");
  const consumer = await deployConsumer(scn, rid);
  const early = await serial("gl-test-2", async () => {
    let reason = "";
    try {
      await caller.simulateWriteContract({ address: consumer, functionName: "perform", args: ["too early"] });
    } catch (e) {
      reason = reasonOf(e);
    }
    return reason;
  });
  if (!early.includes("Refused: no_observation")) throw new Error(`${scn}: the consumer should be refused before the page is observed, got "${early}"`);
  log(scn, `the consumer reading finalized state is refused before the page is observed: "${early}"`);
  const { r: ob, rule: r1 } = await observeOk(scn, rid, "observe");
  if (r1.fact.met !== true || Number(r1.fact.observations) !== 1) throw new Error(`${scn}: the observation must be met`);
  const v1 = await check(rid);
  if (v1.code !== "ok" || !v1.open) throw new Error(`${scn}: after a met observation the rule must be open, got ${v1.code}`);
  log(scn, `the validators read the page and agreed: version is 1.3.0. check (latest state): open=${v1.open} code=${v1.code}`);
  const stillClosed = await serial("gl-test-2", async () => {
    let reason = "";
    try {
      await caller.simulateWriteContract({ address: consumer, functionName: "perform", args: ["not final yet"] });
    } catch (e) {
      reason = reasonOf(e);
    }
    return reason;
  });
  log(scn, `the consumer's finalized read while the observation is not final: ${stillClosed.includes("Refused") ? `"${stillClosed}"` : "(it already finalized)"}`);
  await finalized(scn, ob.hash, "observe");
  const act = await accepted(scn, "gl-test-2", caller, consumer, "perform", ["acted on an open rule with a met fact"], "consumer.perform");
  await okFinish(scn, rid, consumer, Number(r1.fact.observed_at), act.hash);
}

/** What follows the consumer's accepted call: its state read back (polled, a read can lag an accepted write) and the checks on the caller's own clock. */
async function okFinish(scn, rid, consumer, t, actHash) {
  const st = await until(scn, "the consumer's action to read back", async () => {
    const s = await read(consumer, "get_state", []);
    return Number(s.performed) === 1 && s;
  }, 600000);
  log(scn, `the consumer acted once the observation was final: performed=${st.performed}, note "${st.last_note}"`);
  // judged on the caller's own clock
  const edge = await check(rid, t + DAY);
  const late = await check(rid, t + DAY + 1);
  const early2 = await check(rid, t - 1);
  if (edge.code !== "ok" || late.code !== "observation_stale" || early2.code !== "future") throw new Error(`${scn}: the caller's clock was not honoured: ${edge.code} ${late.code} ${early2.code}`);
  log(scn, `on the caller's clock: exactly the allowed age is open; one second later: ${late.code}; before the observation: ${early2.code}`);
  LOG.results.ok = { id: rid, consumer, observed_at: t, edge: edge.code, late: late.code, early: early2.code, performed: st.performed, act: actHash };
}

async function unmet() {
  const scn = "unmet", rid = id("un");
  const c = await rule(scn, rid, NPM, "version", "9.9.9");
  await finalized(scn, c.hash, "create_rule_with_fact");
  const { r: ob, rule: r1 } = await observeOk(scn, rid);
  if (r1.fact.met !== false) throw new Error(`${scn}: the page does not say 9.9.9, the observation must not be met`);
  const v = await check(rid);
  if (v.open || v.code !== "observation_not_met") throw new Error(`${scn}: expected observation_not_met, got ${v.code}`);
  log(scn, `the validators agreed the page does not say 9.9.9: check open=${v.open} code=${v.code}`);
  await finalized(scn, ob.hash, "observe");
  const consumer = await deployConsumer(scn, rid);
  const reason = await serial("gl-test-2", async () => {
    let reason = "";
    try {
      await caller.simulateWriteContract({ address: consumer, functionName: "perform", args: ["should be refused"] });
    } catch (e) {
      reason = reasonOf(e);
    }
    return reason;
  });
  if (!reason.includes("Refused: observation_not_met")) throw new Error(`${scn}: the consumer should be refused with Sluice's reason, got "${reason}"`);
  const rr = await send(caller, consumer, "perform", ["should be refused"]);
  if (ok(rr)) throw new Error(`${scn}: the refused consumer call was accepted`);
  log(scn, `the consumer reading finalized state is refused: "${reason}"`, { hash: rr.hash });
  LOG.results.unmet = { id: rid, consumer, refusal: reason };
}

async function flip() {
  const scn = "flip", rid = id("fl");
  await rule(scn, rid, DEMO, "status", "ok");
  const { rule: r1 } = await observeOk(scn, rid, "observe while the page says degraded");
  const v1 = await check(rid);
  if (r1.fact.met !== false || v1.code !== "observation_not_met") throw new Error(`${scn}: the rule must be closed while the page says degraded, got ${v1.code}`);
  log(scn, `the page said degraded: check open=${v1.open} code=${v1.code}. Waiting for docs/fact-demo.json on main to say "ok" ...`);
  await until(scn, "the demo page to say ok", async () => (await (await fetch(DEMO + "?cb=" + Date.now())).text()).includes('"ok"'), 7200000);
  log(scn, "the page now says ok (as served by raw.githubusercontent.com)");
  const open = await observeUntil(scn, rid, true);
  const v2 = await check(rid);
  if (!v2.open || v2.code !== "ok") throw new Error(`${scn}: the rule must be open once the page says ok, got ${v2.code}`);
  log(scn, `after observation #${open.fact.observations}: check open=${v2.open} code=${v2.code}`);
  log(scn, `now waiting for docs/fact-demo.json to say "degraded" again ...`);
  await until(scn, "the demo page to say degraded again", async () => (await (await fetch(DEMO + "?cb=" + Date.now())).text()).includes('"degraded"'), 7200000);
  const closed = await observeUntil(scn, rid, false);
  const v3 = await check(rid);
  if (v3.open || v3.code !== "observation_not_met") throw new Error(`${scn}: the rule must close again when the page changes back, got ${v3.code}`);
  log(scn, `after observation #${closed.fact.observations}: check open=${v3.open} code=${v3.code} (the rule follows the page in both directions)`);
  LOG.results.flip = { id: rid, observations: closed.fact.observations, followed_the_page: true };
}
/** Observes after the cooldown until the stored answer equals want (the validators may still see a cached copy of the page for a few minutes). */
async function observeUntil(scn, rid, want) {
  for (let a = 1; a <= 10; a++) {
    const r = await getRule(rid);
    const wait = Math.max(0, Number(r.fact.observed_at) + RECHECK + 30 - now());
    if (wait > 0) {
      log(scn, `waiting ${wait}s for the observation cooldown`);
      await sleep(wait * 1000);
    }
    const { rule: after } = await observeOk(scn, rid, `observe (try ${a})`);
    if (after.fact.met === want) return after;
    log(scn, "the validators still read the previous copy of the page: trying again after the cooldown");
  }
  throw new Error(`${scn}: the observation never became ${want}`);
}

async function refusals() {
  const scn = "refusals";
  const plain = id("pl");
  await accepted(scn, "gl-test-1", deployer, S, "create_rule", [plain, BALLAST, "get_latest", JSON.stringify([MONITOR]), "exists,covered", "disputed", "at", BigInt(30 * DAY), "", 0n], `create_rule ${plain} (no fact)`);
  await refused(scn, "gl-test-2", caller, S, "observe", [plain], "no external fact", "observe a rule with no fact");
  const rid = id("ur");
  await rule(scn, rid, MISSING, "version", "1.3.0");
  await refused(scn, "gl-test-2", caller, S, "observe", [rid], "unreadable", "observe a page that does not exist");
  const r = await getRule(rid);
  if (Number(r.fact.observations) !== 0 || Number(r.fact.observed_at) !== 0) throw new Error(`${scn}: an unreadable page must record nothing`);
  const v = await check(rid);
  if (v.code !== "no_observation") throw new Error(`${scn}: the rule must stay closed, got ${v.code}`);
  log(scn, "an unreadable page records nothing; the rule stays closed (no_observation)");
  const bad = await refusedCreate(scn);
  LOG.results.refusals = { plain, unreadable: rid, bad_terms: bad };
}
async function refusedCreate(scn) {
  const out = [];
  for (const [label, args, text] of [
    ["an http url", ["http://registry.npmjs.org/left-pad/1.3.0", "version", "1.3.0", BigInt(DAY)], "https"],
    ["a fact age under a minute", [NPM, "version", "1.3.0", 59n], "fact_max_age"],
    ["an empty value", [NPM, "version", "", BigInt(DAY)], "value must be"],
  ]) {
    let reason = "";
    try {
      await deployer.simulateWriteContract({ address: S, functionName: "create_rule_with_fact", args: [id("bad"), BALLAST, "get_latest", JSON.stringify([MONITOR]), "exists", "", "", 0n, "", 0n, ...args] });
    } catch (e) {
      reason = reasonOf(e);
    }
    if (!reason.includes(text)) throw new Error(`${scn}: ${label} should be refused with "${text}", got "${reason}"`);
    log(scn, `create_rule_with_fact with ${label} refused: "${reason}"`);
    out.push({ label, reason });
  }
  return out;
}

// ------------------------------------------------------------------------------------------------------------------ run

if (process.env.FINISH_OK) {
  // the ok scenario's last steps only: its consumer was called and accepted, the read-back was too early (see okFinish)
  const prev = PREV.events.find((e) => e.scn === "ok" && e.fn === "perform");
  const dep = PREV.events.find((e) => e.scn === "ok" && /consumer deployed/.test(e.msg));
  const obs = (await getRule(id("ok"))).fact;
  await okFinish("ok", id("ok"), dep.address, Number(obs.observed_at), prev?.hash ?? process.env.FINISH_OK);
  LOG.finished = new Date().toISOString();
  writeFileSync(LOGFILE, JSON.stringify(LOG, null, 1));
  console.log("\nRESULTS", JSON.stringify(LOG.results, (_, v) => (typeof v === "bigint" ? v.toString() : v), 1));
  process.exit(0);
}
const all = { ok: okScenario, unmet, flip, refusals };
const only = process.env.ONLY ? process.env.ONLY.split(",") : Object.keys(all);
log("setup", `sluice ${S} on ${NETWORK}, target ${BALLAST} monitor ${MONITOR}`);
const settled = await Promise.allSettled(only.map((k) => all[k]()));
settled.forEach((s, i) => {
  if (s.status === "rejected") log(only[i], `FAILED: ${String(s.reason?.message || s.reason).slice(0, 600)}`);
});
LOG.finished = new Date().toISOString();
writeFileSync(LOGFILE, JSON.stringify(LOG, null, 1));
console.log("\nRESULTS", JSON.stringify(LOG.results, (_, v) => (typeof v === "bigint" ? v.toString() : v), 1));
process.exit(settled.some((s) => s.status === "rejected") ? 1 : 0);
