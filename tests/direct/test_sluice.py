"""Direct-mode tests for Sluice. Direct mode cannot run two contracts against each other, so a FakeTarget answers Sluice's one
cross-contract call and records exactly what it was asked: which address, which method, which arguments and which state. The real
cross-contract path is covered by the live Bradbury run (see README)."""

from datetime import datetime, timezone

import pytest

CONTRACT = "contracts/sluice.py"
TARGET = "0x" + "ab" * 20
LATEST_FINAL = 1
T0 = "2026-01-01T00:00:00Z"
T0_TS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
MAX_AGE = 3600
UNSET = object()


class FakeTarget:
    def __init__(self):
        self.answer = {"exists": True, "covered": True, "disputed": False, "at": T0_TS, "ratio_bps": 10500, "extra": "ignored"}
        self.calls = []
        self.fail = False
        self.junk = UNSET

    def install(self, vm):
        def hook(_vm, request):
            from genlayer.py import calldata

            if "CallContract" not in request:
                return None
            req = request["CallContract"]
            addr = req["address"]
            addr_hex = "0x" + (addr.as_bytes.hex() if hasattr(addr, "as_bytes") else bytes(addr).hex())
            cd = req["calldata"]
            self.calls.append({"address": addr_hex, "method": cd["method"], "args": list(cd["args"]), "state": req.get("state")})
            if self.fail:
                return bytes([1]) + b"simulated target error"
            if addr_hex != TARGET or cd["method"] != "get_latest":
                return bytes([1]) + b"unexpected call"
            return bytes([0]) + calldata.encode(self.junk if self.junk is not UNSET else dict(self.answer))

        vm._gl_call_hook = hook


@pytest.fixture
def S(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    fake = FakeTarget()
    fake.install(direct_vm)
    return direct_vm, direct_deploy(CONTRACT), fake


def _rule(c, rid="r1", target=TARGET, method="get_latest", args='["m1"]', must_true="exists,covered", must_false="disputed", fresh="at", max_age=MAX_AGE, min_field="", min_value=0):
    c.create_rule(rid, target, method, args, must_true, must_false, fresh, max_age, min_field, min_value)


# ---------------------------------------------------------------------------
# create_rule
# ---------------------------------------------------------------------------


def test_a_rule_is_stored_exactly_as_declared(S):
    vm, c, _ = S
    _rule(c, min_field="ratio_bps", min_value=10000)
    r = c.get_rule("r1")
    assert (r["target"].lower(), r["method"], r["args"]) == (TARGET, "get_latest", ["m1"])
    assert (r["must_true"], r["must_false"], r["fresh_field"], r["max_age"]) == (["exists", "covered"], ["disputed"], "at", MAX_AGE)
    assert (r["min_field"], r["min_value"], r["records"], r["last_code"]) == ("ratio_bps", 10000, 0, "")
    assert len(r["digest"]) == 64 and c.get_count() == 1 and c.get_rule_ids(0, 5) == ["r1"]


def test_the_rule_hash_covers_every_term(S):
    vm, c, _ = S
    seen = {}
    variants = {
        "base": {}, "target": {"target": "0x" + "cd" * 20}, "method": {"method": "get_other"}, "args": {"args": '["m2"]'}, "args_int": {"args": "[5]"},
        "true": {"must_true": "exists"}, "false": {"must_false": "disputed,stale"}, "fresh": {"fresh": "seen"}, "age": {"max_age": MAX_AGE + 1},
        "min": {"min_field": "ratio_bps", "min_value": 1}, "min2": {"min_field": "ratio_bps", "min_value": 2},
    }
    for i, (k, kw) in enumerate(variants.items()):
        _rule(c, rid=f"v{i}", **kw)
        seen[k] = c.get_rule(f"v{i}")["digest"]
    assert len(set(seen.values())) == len(seen)
    _rule(c, rid="same")
    assert c.get_rule("same")["digest"] == seen["base"]


def test_rule_ids_are_checked_and_unique(S):
    vm, c, _ = S
    _rule(c)
    with vm.expect_revert("already exists"):
        _rule(c)
    for bad in ("", "x" * 65, "has space", "slash/", "✓", 5):
        with vm.expect_revert("rule_id"):
            _rule(c, rid=bad)
    _rule(c, rid="x" * 64)
    _rule(c, rid="A.b_c-9")


def test_the_target_must_be_a_real_address_and_not_sluice(S):
    vm, c, _ = S
    for bad in ("0x1234", "nothex", 5, None):
        with vm.expect_revert():
            _rule(c, target=bad)
    me = "0x" + vm._contract_address.hex()
    with vm.expect_revert("Sluice itself"):
        _rule(c, target=me)


def test_methods_must_be_plain_lowercase_names(S):
    vm, c, _ = S
    for bad in ("", "Upper", "9lives", "_private", "__class__", "has space", "dash-ed", "x" * 41, "emoji✓", 5):
        with vm.expect_revert("method"):
            _rule(c, method=bad)
    _rule(c, rid="ok40", method="a" * 40)


def test_arguments_are_a_short_json_list_of_strings_and_whole_numbers(S):
    vm, c, _ = S
    for bad in ("not json", "{}", '"x"', "5", "null", '[1,2,3,4,5]', 7):
        with vm.expect_revert("args must be"):
            _rule(c, args=bad)
    for bad in ("[true]", "[null]", "[[1]]", '[{"a":1}]', "[-1]", "[18446744073709551616]", f'["{"x" * 101}"]'):
        with vm.expect_revert("each argument must be"):
            _rule(c, args=bad)
    _rule(c, rid="empty", args="[]")
    _rule(c, rid="four", args='["a", 2, "c", 4]')
    _rule(c, rid="edge", args=f'["{"x" * 100}", 18446744073709551615, 0]')


def test_field_lists_are_checked(S):
    vm, c, _ = S
    for bad in ("Covered", "9a", "a b", "a,,b", ",a", "a,", "a,a", "a,b,c,d,e,f,g", 5, "x" * 33, "_a"):
        with vm.expect_revert():
            _rule(c, must_true=bad)
        with vm.expect_revert():
            _rule(c, must_false=bad)
    _rule(c, rid="six", must_true="a,b,c,d,e,f", must_false="")
    with vm.expect_revert("both required true and required false"):
        _rule(c, must_true="a,b", must_false="b")


def test_freshness_needs_a_field_and_a_sane_age_together(S):
    vm, c, _ = S
    with vm.expect_revert("needs a fresh_field"):
        _rule(c, fresh="", max_age=3600)
    for age in (0, 59, 30 * 86400 + 1, -1, True):
        with vm.expect_revert():
            _rule(c, fresh="at", max_age=age)
    _rule(c, rid="lo", max_age=60)
    _rule(c, rid="hi", max_age=30 * 86400)
    _rule(c, rid="nofresh", fresh="", max_age=0)
    with vm.expect_revert():
        _rule(c, fresh=5)
    with vm.expect_revert():
        _rule(c, fresh="Bad Name")


def test_the_floor_needs_a_field_and_a_sane_value_together(S):
    vm, c, _ = S
    with vm.expect_revert("needs a min_field"):
        _rule(c, min_field="", min_value=5)
    for v in (-1, 10**30 + 1):
        with vm.expect_revert("min_value must be between"):
            _rule(c, min_field="ratio_bps", min_value=v)
    with vm.expect_revert("must be whole numbers"):
        _rule(c, min_field="ratio_bps", min_value=True)
    _rule(c, rid="zero", min_field="ratio_bps", min_value=0)
    _rule(c, rid="max", min_field="ratio_bps", min_value=10**30)
    with vm.expect_revert():
        _rule(c, min_field=5, min_value=0)


def test_a_rule_with_no_condition_is_refused(S):
    vm, c, _ = S
    with vm.expect_revert("at least one condition"):
        _rule(c, must_true="", must_false="", fresh="", max_age=0, min_field="", min_value=0)
    _rule(c, rid="a", must_true="x", must_false="", fresh="", max_age=0)
    _rule(c, rid="b", must_true="", must_false="x", fresh="", max_age=0)
    _rule(c, rid="c", must_true="", must_false="", fresh="at", max_age=100)
    _rule(c, rid="d", must_true="", must_false="", fresh="", max_age=0, min_field="x", min_value=0)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_an_answer_that_meets_every_condition_opens_the_gate(S):
    vm, c, fake = S
    _rule(c, min_field="ratio_bps", min_value=10000)
    v = c.check("r1", T0_TS + 100)
    assert (v["open"], v["code"], v["field"], v["age"]) == (True, "ok", "", 100)


def test_it_reads_only_finalized_state_from_the_right_place(S):
    vm, c, fake = S
    _rule(c)
    c.check("r1", T0_TS)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert (call["address"], call["method"], call["args"], call["state"]) == (TARGET, "get_latest", ["m1"], LATEST_FINAL)


def test_arguments_of_both_kinds_are_passed_through(S):
    vm, c, fake = S
    _rule(c, args='["m1", 7]')
    c.check("r1", T0_TS)
    assert fake.calls[0]["args"] == ["m1", 7]


def test_an_unreadable_target_closes_the_gate(S):
    vm, c, fake = S
    _rule(c)
    fake.fail = True
    v = c.check("r1", T0_TS)
    assert (v["open"], v["code"]) == (False, "unreadable")
    _rule(c, rid="wrong", method="get_other")  # the target answers an error for any other method
    assert c.check("wrong", T0_TS)["code"] == "unreadable"


@pytest.mark.parametrize("junk", [[1, 2], "open", 5, True, None, []])
def test_an_answer_that_is_not_an_object_closes_the_gate(S, junk):
    vm, c, fake = S
    _rule(c)
    fake.junk = junk
    v = c.check("r1", T0_TS)
    assert (v["open"], v["code"]) == (False, "bad_shape")


def test_a_missing_field_closes_the_gate_for_every_kind_of_condition(S):
    vm, c, fake = S
    for field, kw in (
        ("covered", {}), ("disputed", {}), ("at", {}), ("ratio_bps", {"min_field": "ratio_bps", "min_value": 1}),
    ):
        _rule(c, rid="m" + field, **kw)
        ans = dict(fake.answer)
        del ans[field]
        fake.answer = ans
        v = c.check("m" + field, T0_TS)
        assert (v["open"], v["code"], v["field"]) == (False, "missing", field)
        fake.answer = FakeTarget().answer


def test_a_boolean_condition_needs_a_real_boolean(S):
    vm, c, fake = S
    _rule(c)
    for bad in (1, 0, "true", "false", None, [True]):
        fake.answer = dict(FakeTarget().answer, covered=bad)
        v = c.check("r1", T0_TS)
        assert (v["open"], v["code"], v["field"]) == (False, "wrong_type", "covered"), bad
        fake.answer = dict(FakeTarget().answer, disputed=bad)
        v = c.check("r1", T0_TS)
        assert (v["open"], v["code"], v["field"]) == (False, "wrong_type", "disputed"), bad


def test_required_true_and_required_false_are_enforced(S):
    vm, c, fake = S
    _rule(c)
    fake.answer = dict(FakeTarget().answer, covered=False)
    v = c.check("r1", T0_TS)
    assert (v["code"], v["field"]) == ("not_true", "covered")
    fake.answer = dict(FakeTarget().answer, exists=False)
    assert c.check("r1", T0_TS)["field"] == "exists"
    fake.answer = dict(FakeTarget().answer, disputed=True)
    v = c.check("r1", T0_TS)
    assert (v["open"], v["code"], v["field"]) == (False, "not_false", "disputed")


def test_freshness_is_judged_on_the_callers_clock_and_the_boundary_is_inclusive(S):
    vm, c, fake = S
    _rule(c)
    assert c.check("r1", T0_TS + MAX_AGE)["open"] is True
    v = c.check("r1", T0_TS + MAX_AGE + 1)
    assert (v["open"], v["code"], v["field"], v["age"]) == (False, "stale", "at", MAX_AGE + 1)
    assert c.check("r1", T0_TS)["age"] == 0


def test_a_timestamp_in_the_future_closes_the_gate(S):
    vm, c, fake = S
    _rule(c)
    v = c.check("r1", T0_TS - 1)
    assert (v["open"], v["code"]) == (False, "future")


def test_a_timestamp_must_be_a_real_whole_number(S):
    vm, c, fake = S
    _rule(c)
    for bad in (True, "1767225600", None, [T0_TS]):
        fake.answer = dict(FakeTarget().answer, at=bad)
        v = c.check("r1", T0_TS)
        assert (v["open"], v["code"], v["field"]) == (False, "wrong_type", "at"), bad


def test_the_floor_is_inclusive_and_needs_a_real_number(S):
    vm, c, fake = S
    _rule(c, min_field="ratio_bps", min_value=10500)
    assert c.check("r1", T0_TS)["open"] is True
    fake.answer = dict(FakeTarget().answer, ratio_bps=10499)
    v = c.check("r1", T0_TS)
    assert (v["open"], v["code"], v["field"]) == (False, "below_min", "ratio_bps")
    for bad in (True, "10500", None):
        fake.answer = dict(FakeTarget().answer, ratio_bps=bad)
        assert c.check("r1", T0_TS)["code"] == "wrong_type", bad


def test_conditions_are_evaluated_in_a_fixed_order(S):
    vm, c, fake = S
    _rule(c, min_field="ratio_bps", min_value=10**9)
    fake.answer = dict(FakeTarget().answer, covered=False, at=0, ratio_bps=1)
    assert c.check("r1", T0_TS)["code"] == "not_true"
    fake.answer = dict(FakeTarget().answer, at=0, ratio_bps=1)
    assert c.check("r1", T0_TS)["code"] == "stale"
    fake.answer = dict(FakeTarget().answer, ratio_bps=1)
    assert c.check("r1", T0_TS)["code"] == "below_min"


def test_a_rule_with_only_a_floor_ignores_freshness_and_flags(S):
    vm, c, fake = S
    _rule(c, must_true="", must_false="", fresh="", max_age=0, min_field="ratio_bps", min_value=1)
    fake.answer = {"ratio_bps": 5}
    v = c.check("r1", 10**12)
    assert (v["open"], v["age"]) == (True, -1)


def test_check_refuses_bad_input(S):
    vm, c, _ = S
    _rule(c)
    for bad in (True, -1, "now", None):
        with vm.expect_revert():
            c.check("r1", bad)
    with vm.expect_revert("not found"):
        c.check("nope", T0_TS)
    with vm.expect_revert("not found"):
        c.check(5, T0_TS)


def test_check_pinned_accepts_only_the_exact_rule(S):
    vm, c, _ = S
    _rule(c)
    _rule(c, rid="lookalike", must_true="exists")
    digest = c.get_rule("r1")["digest"]
    assert c.check_pinned("r1", digest, T0_TS)["open"] is True
    with vm.expect_revert("not the one expected"):
        c.check_pinned("lookalike", digest, T0_TS)
    with vm.expect_revert("not the one expected"):
        c.check_pinned("r1", "0" * 64, T0_TS)
    with vm.expect_revert():
        c.check_pinned("r1", digest, True)


# ---------------------------------------------------------------------------
# record and views
# ---------------------------------------------------------------------------


def test_record_stores_the_verdict_on_the_transaction_clock(S):
    vm, c, fake = S
    _rule(c)
    c.record("r1")
    r = c.get_rule("r1")
    assert (r["records"], r["last_open"], r["last_code"], r["last_at"]) == (1, True, "ok", T0_TS)
    vm.warp("2026-01-01T02:00:00Z")  # two hours later the answer is older than the limit
    c.record("r1")
    r = c.get_rule("r1")
    assert (r["records"], r["last_open"], r["last_code"], r["last_at"]) == (2, False, "stale", T0_TS + 7200)


def test_record_stores_a_closed_verdict_too_and_never_changes_the_rule(S):
    vm, c, fake = S
    _rule(c)
    before = c.get_rule("r1")
    fake.fail = True
    c.record("r1")
    after = c.get_rule("r1")
    assert (after["last_open"], after["last_code"]) == (False, "unreadable")
    for k in ("target", "method", "args", "must_true", "must_false", "fresh_field", "max_age", "min_field", "min_value", "digest"):
        assert after[k] == before[k]
    with vm.expect_revert("not found"):
        c.record("nope")


def test_paging_is_bounded_and_safe(S):
    vm, c, _ = S
    for i in range(7):
        _rule(c, rid=f"p{i}")
    assert c.get_rule_ids(0, 3) == ["p0", "p1", "p2"]
    assert c.get_rule_ids(5, 10) == ["p5", "p6"]
    assert c.get_rule_ids(7, 10) == []
    for off, lim in ((-1, 5), (0, 0), (0, -1), (True, 5), (0, True), ("0", 5)):
        assert c.get_rule_ids(off, lim) == []
    assert len(c.get_rule_ids(0, 10**6)) == 7


def test_a_page_never_holds_more_than_fifty_ids(S):
    vm, c, _ = S
    for i in range(53):
        _rule(c, rid=f"p{i}")
    assert len(c.get_rule_ids(0, 1000)) == 50
    assert c.get_rule_ids(50, 1000) == ["p50", "p51", "p52"]
    assert c.get_count() == 53


def test_no_entry_point_can_change_a_rule(S):
    _, c, _ = S
    for name in ("set_rule", "update_rule", "delete_rule", "remove_rule", "set_owner", "transfer_ownership", "upgrade", "set_target"):
        assert not hasattr(c, name)


def test_the_only_nondeterministic_step_is_the_validated_page_read_and_only_observe_uses_it():
    src = open(CONTRACT, encoding="utf-8").read()
    for forbidden in ("exec_prompt", "web.render", "web.get", "random"):
        assert forbidden not in src
    assert src.count("run_nondet_unsafe") == 1 and src.count("gl.nondet.web.request") == 1
    assert src.count("_look(") == 2  # the definition and the single call
    observe = src.split("def observe", 1)[1].split("@gl.public", 1)[0]
    assert "_look(" in observe
    # the verdict a consumer reads is deterministic: nothing that evaluates a rule reads the world
    for name in ("_eval", "check", "check_pinned", "record", "_create"):
        body = src.split(f"def {name}(", 1)[1].split("\n    @gl.public", 1)[0].split("\n\n\ndef ", 1)[0]
        assert "_look(" not in body and "nondet" not in body


def test_every_read_of_another_contract_is_finalized_state():
    src = open(CONTRACT, encoding="utf-8").read()
    assert src.count("get_contract_at") == 1
    assert "StorageType.LATEST_FINAL" in src and "LATEST_NON_FINAL" not in src
