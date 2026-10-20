"""Mutation check: break the contract one small way at a time and confirm the direct-mode tests notice.
    python scripts/mutate.py            (all)      MUT=fresh,floor python scripts/mutate.py   (some)
The contract file is restored afterwards, even on Ctrl+C."""

import os
import subprocess
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
py = os.environ.get("PY", sys.executable)
p = os.path.join(root, "contracts", "sluice.py")
orig = open(p, encoding="utf-8", newline="").read()
NL = "\r\n" if "\r\n" in orig else "\n"


def L(text):
    return "        " + text + "\n"


NOW_CHECK = '_bad(not _int(now) or now < 0, "now must be a whole number of seconds")'
WT = 'return _closed("wrong_type", f, f"{f} is not a whole number")'

M = [
    # create_rule: ids, target, method
    ("id may be empty", "not 0 < len(rule_id) <= MAX_ID", "len(rule_id) > MAX_ID"),
    ("id limit constant", "MAX_ID = 64", "MAX_ID = 65"),
    ("id characters unchecked", " or any(c not in ID_CHARS for c in rule_id)", ""),
    ("duplicate id allowed", L('_bad(rule_id in self.rules, "Rule already exists")'), ""),
    ("rule may point at sluice", L('_bad(tgt == gl.message.contract_address, "A rule cannot point at Sluice itself")'), ""),
    ("method unchecked", L('_name(method, "method", MAX_METHOD)'), ""),
    ("method limit constant", "MAX_METHOD = 40", "MAX_METHOD = 41"),
    ("names may start with an underscore", " or n[0] not in LETTERS", ""),
    ("names may hold any character", "any(c not in NAME_CHARS for c in n)", "False"),
    ("names may be empty", "not 1 <= len(n) <= limit", "len(n) > limit"),
    # create_rule: args
    ("args may be any json", "not isinstance(parsed, list) or len(parsed) > MAX_ARGS", "len(parsed) > MAX_ARGS"),
    ("args limit constant", "MAX_ARGS = 4", "MAX_ARGS = 5"),
    ("negative args allowed", "0 <= a < 2**64", "a < 2**64"),
    ("huge args allowed", "0 <= a < 2**64", "0 <= a"),
    ("bool args allowed", "return isinstance(x, int) and not isinstance(x, bool)", "return isinstance(x, int)"),
    ("long string args allowed", "len(a) <= MAX_ARG_LEN", "len(a) <= MAX_ARG_LEN * 10"),
    # create_rule: fields and conditions
    ("field limit constant", "MAX_FIELDS = 6", "MAX_FIELDS = 7"),
    ("duplicate fields allowed", " or len(set(out)) != len(out)", ""),
    ("true and false may overlap", L('_bad(any(x in f for x in t), "a field cannot be both required true and required false")'), ""),
    ("number types unchecked", '_bad(not _int(max_age) or not _int(min_value), "max_age and min_value must be whole numbers")', '_bad(False, "max_age and min_value must be whole numbers")'),
    ("age floor unchecked", "MIN_AGE <= max_age <= MAX_AGE", "max_age <= MAX_AGE"),
    ("age ceiling unchecked", "MIN_AGE <= max_age <= MAX_AGE", "MIN_AGE <= max_age"),
    ("age floor constant", "MIN_AGE = 60", "MIN_AGE = 1"),
    ("age without a field allowed", '_bad(max_age != 0, "max_age needs a fresh_field")', "pass"),
    ("floor without a field allowed", '_bad(min_value != 0, "min_value needs a min_field")', "pass"),
    ("floor below zero allowed", "0 <= min_value <= MAX_FLOOR", "min_value <= MAX_FLOOR"),
    ("floor above the limit allowed", "0 <= min_value <= MAX_FLOOR", "0 <= min_value"),
    ("rule with no condition allowed", '_bad(len(t) + len(f) == 0 and fresh_field == "" and min_field == "", "a rule needs at least one condition")', '_bad(False, "a rule needs at least one condition")'),
    ("hash ignores the target", "[tgt.as_hex.lower(), method, parsed, t, f,", "[method, parsed, t, f,"),
    ("hash ignores the arguments", "method, parsed, t, f,", "method, t, f,"),
    ("hash ignores the floor", "max_age, min_field, min_value]", "max_age, min_field]"),
    ("rule not listed", L("self.rule_ids.append(rule_id)"), ""),
    # check: reading
    ("reads non final state", "view(state=StorageType.LATEST_FINAL)", "view()"),
    ("unreadable counts as open", 'return _closed("unreadable", "", "The target could not be read in finalized state")', 'return {"open": True, "code": "ok", "field": "", "message": "open", "age": -1}'),
    ("shape unchecked", "if not isinstance(ans, dict):", "if False:"),
    # check: conditions
    ("missing required-true field passes", "for f in (json.loads(r.must_true) if r.must_true else []):\n            if f not in ans:", "for f in (json.loads(r.must_true) if r.must_true else []):\n            if False:"),
    ("missing required-false field passes", "for f in (json.loads(r.must_false) if r.must_false else []):\n            if f not in ans:", "for f in (json.loads(r.must_false) if r.must_false else []):\n            if False:"),
    ("missing timestamp passes", "f = r.fresh_field\n            if f not in ans:", "f = r.fresh_field\n            if False:"),
    ("missing floor field passes", "f = r.min_field\n            if f not in ans:", "f = r.min_field\n            if False:"),
    ("required-true type unchecked", 'if type(ans[f]) is not bool:\n                return _closed("wrong_type", f, f"{f} is not a boolean")\n            if not ans[f]:', "if not ans[f]:"),
    ("required-false type unchecked", 'if type(ans[f]) is not bool:\n                return _closed("wrong_type", f, f"{f} is not a boolean")\n            if ans[f]:', "if ans[f]:"),
    ("required-true ignored", "if not ans[f]:", "if False:"),
    ("required-false ignored", "if ans[f]:", "if False:"),
    ("timestamp type unchecked", "if not _int(ans[f]):\n                " + WT + "\n            if ans[f] > now:", "if ans[f] > now:"),
    ("future timestamp passes", "if ans[f] > now:", "if False:"),
    ("age computed backwards", "age = now - ans[f]", "age = ans[f] - now"),
    ("stale boundary exclusive", "if age > int(r.max_age):", "if age >= int(r.max_age):"),
    ("stale ignored", "if age > int(r.max_age):", "if False:"),
    ("floor type unchecked", "if not _int(ans[f]):\n                " + WT + "\n            if ans[f] < int(r.min_value):", "if ans[f] < int(r.min_value):"),
    ("floor boundary exclusive", "if ans[f] < int(r.min_value):", "if ans[f] <= int(r.min_value):"),
    ("floor ignored", "if ans[f] < int(r.min_value):", "if False:"),
    # check: callers
    ("now unchecked in check", "r = self._get(rule_id)\n        " + NOW_CHECK + "\n        return self._eval(r, now)", "r = self._get(rule_id)\n        return self._eval(r, now)"),
    ("pin unchecked", L('_bad(r.digest != expected_digest, "The rule is not the one expected")'), ""),
    ("now unchecked in check_pinned", '"The rule is not the one expected")\n        ' + NOW_CHECK, '"The rule is not the one expected")\n        pass'),
    ("unknown rule not refused", '_bad(not isinstance(i, str) or i not in self.rules, "Rule not found")', '_bad(False, "Rule not found")'),
    # record
    ("record not counted", L("r.records = r.records + 1"), ""),
    ("record openness not stored", L('r.last_open = v["open"]'), ""),
    ("record code not stored", L('r.last_code = v["code"]'), ""),
    ("record time not stored", L("r.last_at = now"), ""),
    ("record uses a zero clock", "now = self._now()\n        v = self._eval(r, now)", "now = 0\n        v = self._eval(r, now)"),
    # views
    ("paging uncapped", "min(limit, PAGE)", "limit"),
    ("paging negative offset", "offset < 0 or isinstance(limit, bool)", "isinstance(limit, bool)"),
    ("paging zero limit", "isinstance(limit, int) or limit < 1:", "isinstance(limit, int) or limit < 0:"),
    ("paging bool offset", "isinstance(offset, bool) or not isinstance(offset, int)", "not isinstance(offset, int)"),
    ("arguments hidden in get_rule", '"args": json.loads(r.args)', '"args": []'),
    ("digest hidden in get_rule", '"digest": r.digest,', '"digest": "",'),
]

results = []
try:
    only = os.environ.get("MUT")
    for name, a, b in M:
        a = a.replace("\n", NL)
        b = b.replace("\n", NL)
        if orig.count(a) != 1:
            print("BAD MUTATION (anchor count %d): %s" % (orig.count(a), name), flush=True)
            continue
        if only and not any(k in name for k in only.split(",")):
            continue
        open(p, "w", encoding="utf-8", newline="").write(orig.replace(a, b))
        r = subprocess.run([py, "-m", "pytest", "tests/direct", "-q", "-x", "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True)
        tag = "CAUGHT  " if r.returncode else "SURVIVED"
        results.append((tag, name))
        print(tag + "  " + name, flush=True)
finally:
    open(p, "w", encoding="utf-8", newline="").write(orig)
print("\n%d caught, %d survived" % (sum(1 for t, _ in results if t.strip() == "CAUGHT"), sum(1 for t, _ in results if t == "SURVIVED")))
