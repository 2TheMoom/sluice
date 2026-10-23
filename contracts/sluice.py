# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from genlayer import *
from genlayer.py.public_abi import StorageType

MAX_ID = 64
MAX_ARGS = 4
MAX_ARG_LEN = 100
MAX_FIELDS = 6
MAX_METHOD = 40
MIN_AGE = 60
MAX_AGE = 30 * 86400
MAX_FLOOR = 10**30
PAGE = 50
ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
LETTERS = "abcdefghijklmnopqrstuvwxyz"
NAME_CHARS = LETTERS + "0123456789_"
MAX_URL = 300
MAX_BODY = 2_000_000  # largest page an observation will read
MAX_PATH = 100
MAX_VALUE = 200
MIN_RECHECK = 300  # seconds between two recorded observations of one rule
HEADERS = {"Accept": "application/json,text/plain,*/*", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
LABEL = "abcdefghijklmnopqrstuvwxyz0123456789-"
SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
INTERNAL = ("local", "internal", "lan", "localhost", "home", "corp", "intranet", "localdomain", "arpa")


def _bad(c: bool, m: str) -> None:
    if c:
        raise gl.vm.UserError(m)


def _addr(x) -> Address:
    if isinstance(x, Address):
        return x
    _bad(not isinstance(x, str), "an address must be a hex string")
    try:
        return Address(x)
    except Exception:
        raise gl.vm.UserError("not a valid address")


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _name(n, what: str, limit: int) -> str:
    _bad(not isinstance(n, str) or not 1 <= len(n) <= limit or any(c not in NAME_CHARS for c in n) or n[0] not in LETTERS, f"{what} must be a plain lowercase name of 1 to {limit} characters, starting with a letter")
    return n


def _names(s, what: str) -> list:
    _bad(not isinstance(s, str), f"{what} must be a comma separated string")
    out = [_name(x, what, 32) for x in s.split(",")] if s != "" else []
    _bad(len(out) > MAX_FIELDS or len(set(out)) != len(out), f"{what}: at most {MAX_FIELDS} different names")
    return out


def _url(u) -> str:
    _bad(not isinstance(u, str) or not u.startswith("https://") or len(u) > MAX_URL, f"url must be https, at most {MAX_URL} characters")
    for ch in u:
        _bad(ch.isspace() or ch in "\"'<>\\`|", "url has a forbidden character")
    h = u[8:].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].lower()
    _bad("@" in h or ":" in h, "url may not have credentials or a port")
    p = h.split(".")
    _bad(len(p) < 2 or p[-1] in INTERNAL or not any(c.isalpha() for c in p[-1]), "url host must be a public domain name")
    for x in p:
        _bad(not 1 <= len(x) <= 63 or x[0] == "-" or x[-1] == "-" or any(c not in LABEL for c in x), "invalid url host")
    return u.split("#", 1)[0]


def _fact(url, path, value, max_age) -> tuple:
    """The external fact a rule may carry, or ("", "", "", 0) for none: one field of a public JSON page must read exactly as value."""
    _bad(any(not isinstance(x, str) for x in (url, path, value)) or not _int(max_age), "fact terms must be strings, and fact_max_age a whole number")
    if url == "":
        _bad(path != "" or value != "" or max_age != 0, "fact terms need a url")
        return ("", "", "", 0)
    u = _url(url)
    _bad(len(path) > MAX_PATH or any(not 1 <= len(s) <= 40 or any(c not in SEG for c in s) for s in path.split(".")), "path is dot-separated names or list positions")  # an empty path is an empty segment
    _bad(not 1 <= len(value) <= MAX_VALUE, f"value must be 1 to {MAX_VALUE} characters")
    _bad(not MIN_AGE <= max_age <= MAX_AGE, f"fact_max_age must be {MIN_AGE} to {MAX_AGE} seconds")
    return (u, path, value, max_age)


def _text(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return json.dumps(v)
    return ""


def _met(body: bytes, path: str, value: str) -> bool:
    """Does the field at the path of the JSON page read exactly as the value?"""
    try:
        cur = json.loads(body.decode("utf-8"))
    except Exception:
        return False
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return False
    return _text(cur) == value


def _look(url: str, path: str, value: str) -> dict:
    """One validator (the leader) fetches the page and proposes whether the fact holds. Every other validator fetches it again on its
    own and votes to accept only if it reaches the same answer. An unreadable page is its own answer, and it records nothing."""
    def leader_fn() -> dict:
        try:
            r = gl.nondet.web.request(url, method="GET", headers=HEADERS)
        except Exception:
            return {"kind": "unreadable", "met": False}
        if r.status != 200 or not r.body or len(r.body) > MAX_BODY:
            return {"kind": "unreadable", "met": False}
        return {"kind": "read", "met": _met(r.body, path, value)}

    def validator_fn(res) -> bool:
        if not isinstance(res, gl.vm.Return):
            return False
        t = res.calldata
        if not isinstance(t, dict) or not isinstance(t.get("kind"), str) or not isinstance(t.get("met"), bool):
            return False
        m = leader_fn()
        return m["kind"] == t["kind"] and m["met"] == t["met"]

    return gl.vm.run_nondet_unsafe(leader_fn, validator_fn)


@allow_storage
@dataclass
class Rule:
    creator: Address
    target: Address
    method: str
    args: str
    must_true: str
    must_false: str
    fresh_field: str
    max_age: u256
    min_field: str
    min_value: u256
    digest: str
    created_at: u256
    records: u256
    last_open: bool
    last_code: str
    last_at: u256
    fact_url: str
    fact_path: str
    fact_value: str
    fact_max_age: u256
    obs_at: u256
    obs_met: bool
    observations: u256


def _closed(code: str, field: str, message: str, age: int = -1) -> dict:
    return {"open": False, "code": code, "field": field, "message": message, "age": age}


class Sluice(gl.Contract):
    rules: TreeMap[str, Rule]
    rule_ids: DynArray[str]

    def __init__(self):
        pass

    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def _get(self, i: str) -> Rule:
        _bad(not isinstance(i, str) or i not in self.rules, "Rule not found")
        return self.rules[i]

    def _eval(self, r: Rule, now: int) -> dict:
        """Reads the target's FINALIZED state and judges the answer on the caller's clock. Fails closed on every doubt."""
        try:
            view = gl.get_contract_at(r.target).view(state=StorageType.LATEST_FINAL)
            ans = getattr(view, r.method)(*json.loads(r.args))
        except Exception:
            return _closed("unreadable", "", "The target could not be read in finalized state")
        if not isinstance(ans, dict):
            return _closed("bad_shape", "", "The target did not answer with an object")
        for f in (json.loads(r.must_true) if r.must_true else []):
            if f not in ans:
                return _closed("missing", f, f"The answer has no field {f}")
            if type(ans[f]) is not bool:
                return _closed("wrong_type", f, f"{f} is not a boolean")
            if not ans[f]:
                return _closed("not_true", f, f"{f} is not true")
        for f in (json.loads(r.must_false) if r.must_false else []):
            if f not in ans:
                return _closed("missing", f, f"The answer has no field {f}")
            if type(ans[f]) is not bool:
                return _closed("wrong_type", f, f"{f} is not a boolean")
            if ans[f]:
                return _closed("not_false", f, f"{f} is not false")
        age = -1
        if r.fresh_field != "":
            f = r.fresh_field
            if f not in ans:
                return _closed("missing", f, f"The answer has no field {f}")
            if not _int(ans[f]):
                return _closed("wrong_type", f, f"{f} is not a whole number")
            if ans[f] > now:
                return _closed("future", f, f"{f} is later than the caller's clock", -1)
            age = now - ans[f]
            if age > int(r.max_age):
                return _closed("stale", f, f"{f} is older than the allowed age", age)
        if r.min_field != "":
            f = r.min_field
            if f not in ans:
                return _closed("missing", f, f"The answer has no field {f}")
            if not _int(ans[f]):
                return _closed("wrong_type", f, f"{f} is not a whole number")
            if ans[f] < int(r.min_value):
                return _closed("below_min", f, f"{f} is below the floor", age)
        if r.fact_url != "":
            if int(r.obs_at) == 0:
                return _closed("no_observation", "", "The page has not been observed yet", age)
            if int(r.obs_at) > now:
                return _closed("future", "observation", "The observation is later than the caller's clock", age)
            oage = now - int(r.obs_at)
            if oage > int(r.fact_max_age):
                return _closed("observation_stale", "", "The observation is older than the allowed age", oage)
            if not r.obs_met:
                return _closed("observation_not_met", "", "The page does not show the expected value", oage)
        return {"open": True, "code": "ok", "field": "", "message": "open", "age": age}

    @gl.public.write
    def create_rule(self, rule_id: str, target: str, method: str, args: str, must_true: str, must_false: str, fresh_field: str, max_age: int, min_field: str, min_value: int) -> None:
        """Declares a rule. It can never be changed. args is a JSON list of up to four strings or whole numbers, the field lists are comma separated."""
        self._create(rule_id, target, method, args, must_true, must_false, fresh_field, max_age, min_field, min_value, ("", "", "", 0))

    @gl.public.write
    def create_rule_with_fact(self, rule_id: str, target: str, method: str, args: str, must_true: str, must_false: str, fresh_field: str, max_age: int, min_field: str, min_value: int, fact_url: str, fact_path: str, fact_value: str, fact_max_age: int) -> None:
        """The same rule, plus one external fact fixed now: a field of a public JSON page that must read exactly as fact_value. The
        validators settle it with observe(), and the rule is open only while that observation is met and no older than fact_max_age."""
        self._create(rule_id, target, method, args, must_true, must_false, fresh_field, max_age, min_field, min_value, _fact(fact_url, fact_path, fact_value, fact_max_age))

    def _create(self, rule_id: str, target: str, method: str, args: str, must_true: str, must_false: str, fresh_field: str, max_age: int, min_field: str, min_value: int, fact: tuple) -> None:
        _bad(not isinstance(rule_id, str) or not 0 < len(rule_id) <= MAX_ID or any(c not in ID_CHARS for c in rule_id), f"rule_id must be 1 to {MAX_ID} characters from a-z A-Z 0-9 . _ -")
        _bad(rule_id in self.rules, "Rule already exists")
        tgt = _addr(target)
        _bad(tgt == gl.message.contract_address, "A rule cannot point at Sluice itself")
        _name(method, "method", MAX_METHOD)
        try:
            parsed = json.loads(args)
        except Exception:
            raise gl.vm.UserError("args must be a JSON list")
        _bad(not isinstance(parsed, list) or len(parsed) > MAX_ARGS, f"args must be a list of at most {MAX_ARGS} items")
        for a in parsed:
            _bad(not (_int(a) and 0 <= a < 2**64) and not (isinstance(a, str) and len(a) <= MAX_ARG_LEN), f"each argument must be a whole number or a string of at most {MAX_ARG_LEN} characters")
        t, f = _names(must_true, "must_true"), _names(must_false, "must_false")
        _bad(any(x in f for x in t), "a field cannot be both required true and required false")
        _bad(not _int(max_age) or not _int(min_value), "max_age and min_value must be whole numbers")
        if fresh_field != "":
            _name(fresh_field, "fresh_field", 32)
            _bad(not MIN_AGE <= max_age <= MAX_AGE, f"max_age must be {MIN_AGE} to {MAX_AGE} seconds")
        else:
            _bad(max_age != 0, "max_age needs a fresh_field")
        if min_field != "":
            _name(min_field, "min_field", 32)
            _bad(not 0 <= min_value <= MAX_FLOOR, "min_value must be between 0 and 10^30")
        else:
            _bad(min_value != 0, "min_value needs a min_field")
        _bad(len(t) + len(f) == 0 and fresh_field == "" and min_field == "", "a rule needs at least one condition")
        digest = hashlib.sha256(json.dumps([tgt.as_hex.lower(), method, parsed, t, f, fresh_field, max_age, min_field, min_value, fact[0], fact[1], fact[2], fact[3]]).encode("utf-8")).hexdigest()
        self.rules[rule_id] = Rule(
            creator=gl.message.sender_address, target=tgt, method=method, args=json.dumps(parsed), must_true=json.dumps(t), must_false=json.dumps(f),
            fresh_field=fresh_field, max_age=max_age, min_field=min_field, min_value=min_value, digest=digest, created_at=self._now(),
            records=0, last_open=False, last_code="", last_at=0,
            fact_url=fact[0], fact_path=fact[1], fact_value=fact[2], fact_max_age=fact[3], obs_at=0, obs_met=False, observations=0,
        )
        self.rule_ids.append(rule_id)

    @gl.public.write
    def observe(self, rule_id: str) -> None:
        """Permissionless. Validators fetch the page this rule names and agree on whether its field reads as the rule expects. The agreed
        answer and the time are stored, and check() needs it to be met and fresh. A page that cannot be read records nothing."""
        r = self._get(rule_id)
        _bad(r.fact_url == "", "This rule has no external fact")
        now = self._now()
        _bad(int(r.obs_at) > 0 and now < int(r.obs_at) + MIN_RECHECK, "Observed too recently, try again later")
        res = _look(r.fact_url, r.fact_path, r.fact_value)
        _bad(res["kind"] != "read", "Page unreadable, nothing recorded. Try again shortly.")
        r.obs_at = now
        r.obs_met = res["met"]
        r.observations += 1

    @gl.public.view
    def check(self, rule_id: str, now: int) -> dict:
        """Reads the target in finalized state. now is the caller's own clock in seconds: a consumer contract passes its own."""
        r = self._get(rule_id)
        _bad(not _int(now) or now < 0, "now must be a whole number of seconds")
        return self._eval(r, now)

    @gl.public.view
    def check_pinned(self, rule_id: str, expected_digest: str, now: int) -> dict:
        """Like check, but refuses unless the rule is exactly the one the caller expects."""
        r = self._get(rule_id)
        _bad(r.digest != expected_digest, "The rule is not the one expected")
        _bad(not _int(now) or now < 0, "now must be a whole number of seconds")
        return self._eval(r, now)

    @gl.public.write
    def record(self, rule_id: str) -> None:
        """Evaluates the rule on this transaction's clock and stores the verdict where an explorer can show it. Informational: a consumer
        should call check with its own clock, because a stored verdict is only as fresh as its last_at."""
        r = self._get(rule_id)
        now = self._now()
        v = self._eval(r, now)
        r.records = r.records + 1
        r.last_open = v["open"]
        r.last_code = v["code"]
        r.last_at = now

    @gl.public.view
    def get_rule(self, rule_id: str) -> dict:
        r = self._get(rule_id)
        return {
            "id": rule_id, "creator": r.creator.as_hex, "target": r.target.as_hex, "method": r.method, "args": json.loads(r.args),
            "must_true": json.loads(r.must_true), "must_false": json.loads(r.must_false), "fresh_field": r.fresh_field, "max_age": int(r.max_age),
            "min_field": r.min_field, "min_value": int(r.min_value), "digest": r.digest, "created_at": int(r.created_at),
            "records": int(r.records), "last_open": r.last_open, "last_code": r.last_code, "last_at": int(r.last_at),
            "fact": {"url": r.fact_url, "path": r.fact_path, "value": r.fact_value, "max_age": int(r.fact_max_age), "observed_at": int(r.obs_at), "met": r.obs_met, "observations": int(r.observations)},
        }

    @gl.public.view
    def get_rule_ids(self, offset: int, limit: int) -> list:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return []
        return [self.rule_ids[k] for k in range(offset, min(offset + min(limit, PAGE), len(self.rule_ids)))]

    @gl.public.view
    def get_count(self) -> int:
        return len(self.rule_ids)
