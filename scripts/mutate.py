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
    ("age floor unchecked", "_bad(not MIN_AGE <= max_age <= MAX_AGE, f\"max_age must be", "_bad(not max_age <= MAX_AGE, f\"max_age must be"),
    ("age ceiling unchecked", "_bad(not MIN_AGE <= max_age <= MAX_AGE, f\"max_age must be", "_bad(not MIN_AGE <= max_age, f\"max_age must be"),
    ("age floor constant", "MIN_AGE = 60", "MIN_AGE = 1"),
    ("age without a field allowed", '_bad(max_age != 0, "max_age needs a fresh_field")', "pass"),
    ("floor without a field allowed", '_bad(min_value != 0, "min_value needs a min_field")', "pass"),
    ("floor below zero allowed", "0 <= min_value <= MAX_FLOOR", "min_value <= MAX_FLOOR"),
    ("floor above the limit allowed", "0 <= min_value <= MAX_FLOOR", "0 <= min_value"),
    ("rule with no condition allowed", '_bad(len(t) + len(f) == 0 and fresh_field == "" and min_field == "", "a rule needs at least one condition")', '_bad(False, "a rule needs at least one condition")'),
    ("hash ignores the target", "[tgt.as_hex.lower(), method, parsed, t, f,", "[method, parsed, t, f,"),
    ("hash ignores the arguments", "method, parsed, t, f,", "method, t, f,"),
    ("hash ignores the floor", "max_age, min_field, min_value, fact[0]", "max_age, min_field, fact[0]"),
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
    # the external fact: the terms
    ("fact: create_rule_with_fact ignores its terms", "_fact(fact_url, fact_path, fact_value, fact_max_age))", '("", "", "", 0))'),
    ("fact: types unchecked", '    _bad(any(not isinstance(x, str) for x in (url, path, value)) or not _int(max_age), "fact terms must be strings, and fact_max_age a whole number")\n', ""),
    ("fact: max_age type unchecked", " or not _int(max_age), \"fact terms must be strings", ", \"fact terms must be strings"),
    ("fact: terms without a url allowed", '        _bad(path != "" or value != "" or max_age != 0, "fact terms need a url")\n', ""),
    ("fact: path unbounded", "_bad(len(path) > MAX_PATH or any(", "_bad(any("),
    ("fact: empty path allowed", "not 1 <= len(s) <= 40 or any(c not in SEG for c in s) for s in path.split", "len(s) > 40 or any(c not in SEG for c in s) for s in path.split"),
    ("fact: segment length unchecked", "not 1 <= len(s) <= 40 or any(c not in SEG for c in s)", "any(c not in SEG for c in s)"),
    ("fact: segment length ceiling", "not 1 <= len(s) <= 40", "len(s) < 1"),
    ("fact: segment floor", "not 1 <= len(s) <= 40", "len(s) > 40"),
    ("fact: segment characters unchecked", "or any(c not in SEG for c in s)", ""),
    ("fact: segment alphabet gains a slash", 'SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"', 'SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-/"'),
    ("fact: segment alphabet gains a space", 'SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"', 'SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- "'),
    ("fact: value may be empty", "not 1 <= len(value) <= MAX_VALUE", "len(value) > MAX_VALUE"),
    ("fact: value unbounded", "not 1 <= len(value) <= MAX_VALUE", "len(value) < 1"),
    ("fact: value limit constant", "MAX_VALUE = 200", "MAX_VALUE = 201"),
    ("fact: path limit constant", "MAX_PATH = 100", "MAX_PATH = 101"),
    ("fact: age unchecked", '    _bad(not MIN_AGE <= max_age <= MAX_AGE, f"fact_max_age must be {MIN_AGE} to {MAX_AGE} seconds")\n', ""),
    ("fact: age floor", "_bad(not MIN_AGE <= max_age <= MAX_AGE, f\"fact_max_age", "_bad(max_age > MAX_AGE, f\"fact_max_age"),
    ("fact: age ceiling", "_bad(not MIN_AGE <= max_age <= MAX_AGE, f\"fact_max_age", "_bad(max_age < MIN_AGE, f\"fact_max_age"),
    ("fact: url not normalised", "    u = _url(url)\n", "    u = url\n"),
    ("fact: path and value swapped", "return (u, path, value, max_age)", "return (u, value, path, max_age)"),
    ("fact: url not stored", "fact_url=fact[0],", 'fact_url="",'),
    ("fact: path not stored", "fact_path=fact[1],", 'fact_path="",'),
    ("fact: value not stored", "fact_value=fact[2],", 'fact_value="",'),
    ("fact: age not stored", "fact_max_age=fact[3],", "fact_max_age=0,"),
    ("fact: url not in the hash", ", fact[0], fact[1], fact[2], fact[3]]", ", fact[1], fact[2], fact[3]]"),
    ("fact: path not in the hash", ", fact[0], fact[1], fact[2], fact[3]]", ", fact[0], fact[2], fact[3]]"),
    ("fact: value not in the hash", ", fact[0], fact[1], fact[2], fact[3]]", ", fact[0], fact[1], fact[3]]"),
    ("fact: age not in the hash", ", fact[0], fact[1], fact[2], fact[3]]", ", fact[0], fact[1], fact[2]]"),
    # the external fact: the url
    ("url: scheme unchecked", 'not u.startswith("https://") or', ""),
    ("url: length unchecked", " or len(u) > MAX_URL", ""),
    ("url: length constant", "MAX_URL = 300", "MAX_URL = 301"),
    ("url: forbidden characters allowed", 'ch.isspace() or ch in "\\"\'<>\\\\`|"', "False"),
    ("url: whitespace allowed", "ch.isspace() or ch in", "ch in"),
    ("url: credentials allowed", '_bad("@" in h or ":" in h,', '_bad(":" in h,'),
    ("url: port allowed", '_bad("@" in h or ":" in h,', '_bad("@" in h,'),
    ("url: one-label host allowed", "len(p) < 2 or", ""),
    ("url: internal suffix allowed", "p[-1] in INTERNAL or", ""),
    ("url: numeric suffix allowed", " or not any(c.isalpha() for c in p[-1])", ""),
    ("url: label length unchecked", "not 1 <= len(x) <= 63 or", ""),
    ("url: label dash edges allowed", 'x[0] == "-" or x[-1] == "-" or', ""),
    ("url: label characters unchecked", " or any(c not in LABEL for c in x)", ""),
    ("url: fragment kept", 'return u.split("#", 1)[0]', "return u"),
    # the external fact: reading the page
    ("page: bad json counts as met", "    except Exception:\n        return False\n    for seg", "    except Exception:\n        return True\n    for seg"),
    ("page: missing key counts as met", "        else:\n            return False\n    return _text(cur) == value", "        else:\n            return True\n    return _text(cur) == value"),
    ("page: dict key unchecked", "isinstance(cur, dict) and seg in cur", "isinstance(cur, dict)"),
    ("page: list position unchecked", "seg.isdigit() and int(seg) < len(cur)", "seg.isdigit()"),
    ("page: list bound unchecked", "seg.isdigit() and int(seg) < len(cur)", "int(seg) < len(cur)"),
    ("page: lists ignored", "elif isinstance(cur, list) and", "elif False and"),
    ("page: compare ignores case", "return _text(cur) == value", "return _text(cur).lower() == value.lower()"),
    ("page: compare ignores the value", "return _text(cur) == value", "return True"),
    ("page: true reads as True", 'return "true" if v else "false"', 'return "True" if v else "False"'),
    ("page: null reads as None", 'return "null"', 'return "None"'),
    ("page: containers read as text", '        return json.dumps(v)\n    return ""', '        return json.dumps(v)\n    return str(v)'),
    ("page: status unchecked", "r.status != 200 or not r.body", "not r.body"),
    ("page: empty body allowed", "r.status != 200 or not r.body or", "r.status != 200 or"),
    ("page: size unchecked", " or len(r.body) > MAX_BODY", ""),
    ("page: size constant", "MAX_BODY = 2_000_000", "MAX_BODY = 2_000_001"),
    ("page: a failed request reads as a page", 'except Exception:\n            return {"kind": "unreadable", "met": False}', 'except Exception:\n            return {"kind": "read", "met": False}'),
    ("page: unreadable reads as met", 'if r.status != 200 or not r.body or len(r.body) > MAX_BODY:\n            return {"kind": "unreadable", "met": False}', 'if r.status != 200 or not r.body or len(r.body) > MAX_BODY:\n            return {"kind": "unreadable", "met": True}'),
    # the vote
    ("vote: validator ignores met", 'return m["kind"] == t["kind"] and m["met"] == t["met"]', 'return m["kind"] == t["kind"]'),
    ("vote: validator ignores kind", 'return m["kind"] == t["kind"] and m["met"] == t["met"]', 'return m["met"] == t["met"]'),
    ("vote: validator accepts anything", 'return m["kind"] == t["kind"] and m["met"] == t["met"]', "return True"),
    ("vote: validator accepts errors", "        if not isinstance(res, gl.vm.Return):\n            return False\n        t = res.calldata", "        if not isinstance(res, gl.vm.Return):\n            return True\n        t = res.calldata"),
    ("vote: shape unchecked", 'if not isinstance(t, dict) or not isinstance(t.get("kind"), str) or not isinstance(t.get("met"), bool):\n            return False\n', ""),
    ("vote: bool unchecked", ' or not isinstance(t.get("met"), bool)', ""),
    ("vote: kind type unchecked", ' or not isinstance(t.get("kind"), str)', ""),
    # observe
    ("observe: without a fact", '        _bad(r.fact_url == "", "This rule has no external fact")\n', ""),
    ("observe: no rate limit", '        _bad(int(r.obs_at) > 0 and now < int(r.obs_at) + MIN_RECHECK, "Observed too recently, try again later")\n', ""),
    ("observe: rate limit off by one", "now < int(r.obs_at) + MIN_RECHECK", "now <= int(r.obs_at) + MIN_RECHECK"),
    ("observe: rate limit constant", "MIN_RECHECK = 300", "MIN_RECHECK = 3"),
    ("observe: unreadable recorded", '        _bad(res["kind"] != "read", "Page unreadable, nothing recorded. Try again shortly.")\n', ""),
    ("observe: time not recorded", "        r.obs_at = now\n", ""),
    ("observe: answer not recorded", '        r.obs_met = res["met"]\n', ""),
    ("observe: answer always met", '        r.obs_met = res["met"]\n', "        r.obs_met = True\n"),
    ("observe: observations not counted", "        r.observations += 1\n", ""),
    ("observe: reads another page", "_look(r.fact_url, r.fact_path, r.fact_value)", "_look(r.fact_url, r.fact_path, r.fact_path)"),
    # check: what the observation must satisfy
    ("check: no observation ignored", '            if int(r.obs_at) == 0:\n                return _closed("no_observation", "", "The page has not been observed yet", age)\n', ""),
    ("check: future observation ignored", '            if int(r.obs_at) > now:\n                return _closed("future", "observation", "The observation is later than the caller\'s clock", age)\n', ""),
    ("check: stale observation ignored", '            if oage > int(r.fact_max_age):\n                return _closed("observation_stale", "", "The observation is older than the allowed age", oage)\n', ""),
    ("check: stale one second late", "if oage > int(r.fact_max_age):", "if oage > int(r.fact_max_age) + 1:"),
    ("check: stale one second early", "if oage > int(r.fact_max_age):", "if oage >= int(r.fact_max_age):"),
    ("check: unmet observation ignored", '            if not r.obs_met:\n                return _closed("observation_not_met", "", "The page does not show the expected value", oage)\n', ""),
    ("check: fact skipped for rules that have one", '        if r.fact_url != "":\n            if int(r.obs_at) == 0:', '        if False:\n            if int(r.obs_at) == 0:'),
    ("check: observation age wrong", "oage = now - int(r.obs_at)", "oage = 0"),
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
