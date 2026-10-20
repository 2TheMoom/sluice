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
        return {"open": True, "code": "ok", "field": "", "message": "open", "age": age}

    @gl.public.write
    def create_rule(self, rule_id: str, target: str, method: str, args: str, must_true: str, must_false: str, fresh_field: str, max_age: int, min_field: str, min_value: int) -> None:
        """Declares a rule. It can never be changed. args is a JSON list of up to four strings or whole numbers, the field lists are comma separated."""
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
        digest = hashlib.sha256(json.dumps([tgt.as_hex.lower(), method, parsed, t, f, fresh_field, max_age, min_field, min_value]).encode("utf-8")).hexdigest()
        self.rules[rule_id] = Rule(
            creator=gl.message.sender_address, target=tgt, method=method, args=json.dumps(parsed), must_true=json.dumps(t), must_false=json.dumps(f),
            fresh_field=fresh_field, max_age=max_age, min_field=min_field, min_value=min_value, digest=digest, created_at=self._now(),
            records=0, last_open=False, last_code="", last_at=0,
        )
        self.rule_ids.append(rule_id)

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
        }

    @gl.public.view
    def get_rule_ids(self, offset: int, limit: int) -> list:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return []
        return [self.rule_ids[k] for k in range(offset, min(offset + min(limit, PAGE), len(self.rule_ids)))]

    @gl.public.view
    def get_count(self) -> int:
        return len(self.rule_ids)
