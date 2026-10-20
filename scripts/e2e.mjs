// Live end to end run of Sluice on Bradbury against the real, deployed Ballast contract (https://github.com/2TheMoom/ballast).
//   SLUICE=0x.. RUN=v1 node scripts/e2e.mjs        writes scripts/e2e-log-<RUN>.json
//
// Seven rules over real Ballast monitors, each checked with the contract's own answer, then recorded on chain. After the rules have
// FINALIZED, two small consumer contracts read Sluice's answer from finalized state: one acts, one is refused with Sluice's reason.
// That second part is the nested finalized read (consumer -> Sluice -> Ballast).
import { existsSync, readFileSync, writeFileSync } from "fs";
import { WALLETS, clientFor, read, reader, reasonOf, send, sleep, ts } from "./lib.mjs";

const S = process.env.SLUICE;
const RUN = process.env.RUN || Date.now().toString(36);
const BALLAST = "0xd1590EBBA44Cd2d00e003053811E47b9d2209668";
const deployer = clientFor("gl-test-1");
const caller = clientFor("gl-test-2");
// A run that stopped part way (a script error, never a contract one) can be resumed: rules and records already on chain are not sent again.
const LOGFILE = `scripts/e2e-log-${RUN}.json`;
const PREV = process.env.RESUME && existsSync(LOGFILE) ? JSON.parse(readFileSync(LOGFILE, "utf8")) : null;
const LOG = { run: RUN, sluice: S, ballast: BALLAST, started: PREV?.started ?? new Date().toISOString(), events: PREV?.events ?? [], rules: {}, consumers: {} };
const prevHash = (id) => PREV?.events.find((e) => e.fn === "create_rule" && e.msg.startsWith(`create_rule ${id} `) && (e.status === "ACCEPTED" || e.status === "FINALIZED"))?.hash;
const log = (msg, extra) => {
  console.log(`[${ts()}] ${msg}`);
  LOG.events.push({ t: new Date().toISOString(), msg, ...(extra || {}) });
};
const ok = (r) => (r.status === "ACCEPTED" || r.status === "FINALIZED") && r.exec === "FINISHED_WITH_RETURN";
const nowTs = () => Math.floor(Date.now() / 1000);

const DAY = 86400n;
// id, ballast monitor, must_true, must_false, fresh field, max age, floor field, floor, expected code
const RULES = [
  ["covered-30d", "e2e-v2-covered", "exists,covered", "disputed", "at", 30n * DAY, "", 0n, "ok"],
  ["covered-1h", "e2e-v2-covered", "exists,covered", "disputed", "at", 3600n, "", 0n, "stale"],
  ["strict", "e2e-v2-strict", "exists,covered", "disputed", "at", 30n * DAY, "", 0n, "not_true"],
  ["floor-ok", "e2e-v2-twochains", "exists,covered", "disputed", "at", 30n * DAY, "ratio_bps", 11000n, "ok"],
  ["floor-high", "e2e-v2-twochains", "exists,covered", "disputed", "at", 30n * DAY, "ratio_bps", 12000n, "below_min"],
  ["no-monitor", "no-such-monitor", "exists,covered", "disputed", "at", 30n * DAY, "", 0n, "unreadable"],
  ["real-wallet", "mm-weth-base-1", "exists,covered", "disputed", "at", DAY, "ratio_bps", 10000n, "ok"],
];

async function finalized(hash, label) {
  const t0 = Date.now();
  for (let a = 0; a < 30; a++) {
    try {
      await reader.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 400, interval: 10000 });
      log(`${label} FINALIZED ${((Date.now() - t0) / 60000).toFixed(1)} min after the wait began`);
      return;
    } catch {
      await sleep(15000);
    }
  }
  throw new Error(`${label} did not finalize`);
}

async function accepted(client, address, fn, args, label) {
  for (let a = 1; a <= 6; a++) {
    const r = await send(client, address, fn, args);
    log(`${label} ${r.hash.slice(0, 12)} ${r.status} exec=${r.exec}`, { fn, hash: r.hash, status: r.status, exec: r.exec });
    if (ok(r)) return r;
    await sleep(20000);
  }
  throw new Error(`${label} was never accepted`);
}

async function deploy(client, file, args, label) {
  const code = new Uint8Array(readFileSync(file));
  const hash = await client.deployContract({ code, args });
  const rec = await client.waitForTransactionReceipt({ hash, status: "ACCEPTED", retries: 200, interval: 5000 });
  const address = rec.txDataDecoded?.contractAddress ?? rec.data?.contract_address;
  log(`${label} deployed: ${address}`, { hash, address });
  return { address, hash };
}

try {
  log(`sluice ${S}, target ${BALLAST}`);
  // 1. the rules
  const created = [];
  for (const [id, monitor, mt, mf, fresh, age, floorField, floor] of RULES) {
    if (prevHash(id)) {
      created.push([id, prevHash(id)]);
      log(`create_rule ${id} already on chain (resumed)`);
      continue;
    }
    const r = await accepted(deployer, S, "create_rule", [id, BALLAST, "get_latest", JSON.stringify([monitor]), mt, mf, fresh, age, floorField, floor], `create_rule ${id}`);
    created.push([id, r.hash]);
  }
  // 2. the verdicts, read from Sluice with the caller's own clock
  const now = nowTs();
  for (const [id, , , , , , , , expected] of RULES) {
    const v = await read(S, "check", [id, now]);
    log(`check ${id.padEnd(12)} -> open=${v.open} code=${v.code}${v.field ? " field=" + v.field : ""} age=${v.age}  ${v.message}`, { id, verdict: v });
    if (v.code !== expected) throw new Error(`rule ${id}: expected ${expected}, got ${v.code}`);
    LOG.rules[id] = { verdict: v, expected };
  }
  // 3. bad calls are refused
  const digest = (await read(S, "get_rule", ["covered-30d"])).digest;
  for (const [fn, args, expect] of [["check_pinned", ["covered-30d", "0".repeat(64), now], "not the one expected"], ["check", ["covered-30d", -1], "now must be"], ["check", ["nope", now], "not found"]]) {
    let reason = "ACCEPTED";
    try {
      await read(S, fn, args);
    } catch (e) {
      reason = reasonOf(e);
    }
    if (!reason.includes(expect)) throw new Error(`${fn} should have been refused with "${expect}", got "${reason}"`);
    log(`${fn} refused: "${reason}"`);
  }
  const pinned = await read(S, "check_pinned", ["covered-30d", digest, now]);
  if (pinned.code !== "ok") throw new Error("the pinned check should be open");
  log(`check_pinned with the right hash -> ${pinned.code}`);
  // 4. record the verdicts on chain
  for (const [id] of RULES) {
    const want = LOG.rules[id].expected;
    let r = await read(S, "get_rule", [id]);
    if (Number(r.records) === 0) {
      await accepted(caller, S, "record", [id], `record ${id}`);
      // a read-back can lag a just-accepted write: poll before judging it
      for (let i = 0; i < 40 && Number(r.records) === 0; i++) {
        await sleep(5000);
        r = await read(S, "get_rule", [id]);
      }
    }
    if (r.last_code !== want || Number(r.records) !== 1) throw new Error(`record ${id}: stored ${r.last_code} x${r.records}, expected ${want}`);
    LOG.rules[id].recorded = { last_open: r.last_open, last_code: r.last_code, last_at: r.last_at };
  }
  log("every verdict recorded on chain matches the one read");
  // 5. consumers, once the rules are final
  const last = created[created.length - 1];
  log("waiting for the rules to finalize before a consumer reads them");
  await finalized(last[1], `create_rule ${last[0]}`);
  const open = await deploy(deployer, "contracts/test_support/sluice_consumer.py", [S, "covered-30d", digest], "consumer (open rule)");
  const strictDigest = (await read(S, "get_rule", ["strict"])).digest;
  const shut = await deploy(deployer, "contracts/test_support/sluice_consumer.py", [S, "strict", strictDigest], "consumer (closed rule)");
  const act = await accepted(caller, open.address, "perform", ["acted on an open rule"], "consumer.perform (open rule)");
  const st = await read(open.address, "get_state", []);
  if (st.performed !== 1 && st.performed !== "1") throw new Error(`the open consumer did not act: ${JSON.stringify(st)}`);
  log(`the consumer acted: performed=${st.performed}, note "${st.last_note}"`);
  let reason = "SIMULATION ACCEPTED";
  try {
    await caller.simulateWriteContract({ address: shut.address, functionName: "perform", args: ["should be refused"] });
  } catch (e) {
    reason = reasonOf(e);
  }
  if (!reason.includes("Refused: not_true covered")) throw new Error(`the closed consumer should have been refused with Sluice's reason, got "${reason}"`);
  const refused = await send(caller, shut.address, "perform", ["should be refused"]);
  if (ok(refused)) throw new Error("the refused consumer call was accepted");
  log(`the closed consumer was refused: "${reason}"`, { hash: refused.hash });
  LOG.consumers = { open: { ...open, act: act.hash }, closed: { ...shut, refused: refused.hash } };
  LOG.finished = new Date().toISOString();
  console.log("E2E OK");
} catch (e) {
  LOG.error = String(e.message || e);
  console.error("E2E FAILED:", LOG.error);
  process.exitCode = 1;
} finally {
  writeFileSync(`scripts/e2e-log-${RUN}.json`, JSON.stringify(LOG, null, 1));
}
