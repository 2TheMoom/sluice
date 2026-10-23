"""Direct-mode tests for Sluice's external fact (create_rule_with_fact, observe) and the validator vote inside observe.

A rule may carry one fact about the world: one field of a public JSON page must read exactly as the rule says. The validators settle it
with observe(): the leader fetches the page and proposes an answer, every other validator fetches it again on its own and accepts only
the same answer. check() then stays deterministic: it needs the stored observation to be met and no older than the rule allows."""

import copy

import pytest

from tests.direct.test_sluice import MAX_AGE, T0, T0_TS, TARGET, FakeTarget

CONTRACT = "contracts/sluice.py"
API = "https://status.example.com/v1/health"
FACT_AGE = 1800
RECHECK = 300
OK = '{"status": "ok"}'
DOWN = '{"status": "down"}'


def _serve(vm, body, status=200, url=API):
    vm.clear_mocks()
    vm.mock_web("^" + url.replace(".", r"\.") + "$", {"method": "GET", "status": status, "body": body})


def _rule(c, rid="f1", must_true="exists,covered", must_false="disputed", fresh="at", max_age=MAX_AGE, url=API, path="status", value="ok", fact_age=FACT_AGE):
    c.create_rule_with_fact(rid, TARGET, "get_latest", '["m1"]', must_true, must_false, fresh, max_age, "", 0, url, path, value, fact_age)


@pytest.fixture
def F(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    fake = FakeTarget()
    fake.install(direct_vm)
    c = direct_deploy(CONTRACT)
    _rule(c)
    _serve(direct_vm, OK)
    return direct_vm, c, fake


def _leader(vm):
    return copy.deepcopy(vm._captured_validators[-1][0])


# ---------------------------------------------------------------------------------------------------------------- declaring


def test_a_rule_with_a_fact_stores_it_exactly_as_declared(F):
    vm, c, _ = F
    r = c.get_rule("f1")
    assert r["fact"] == {"url": API, "path": "status", "value": "ok", "max_age": FACT_AGE, "observed_at": 0, "met": False, "observations": 0}
    assert (r["must_true"], r["must_false"], r["fresh_field"]) == (["exists", "covered"], ["disputed"], "at")


def test_a_plain_rule_has_no_fact_and_cannot_be_observed(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    c.create_rule("p1", TARGET, "get_latest", '["m1"]', "exists", "", "", 0, "", 0)
    assert c.get_rule("p1")["fact"] == {"url": "", "path": "", "value": "", "max_age": 0, "observed_at": 0, "met": False, "observations": 0}
    with direct_vm.expect_revert("no external fact"):
        c.observe("p1")


def test_the_rule_hash_covers_every_fact_term(F):
    vm, c, _ = F
    base = c.get_rule("f1")["digest"]
    seen = {"base": base}
    variants = {
        "url": {"url": "https://other.example.com/v1/health"}, "path": {"path": "state"}, "value": {"value": "fine"}, "age": {"fact_age": FACT_AGE + 1},
    }
    for i, (k, kw) in enumerate(variants.items()):
        _rule(c, rid=f"v{i}", **kw)
        seen[k] = c.get_rule(f"v{i}")["digest"]
    c.create_rule("plain", TARGET, "get_latest", '["m1"]', "exists,covered", "disputed", "at", MAX_AGE, "", 0)
    seen["plain"] = c.get_rule("plain")["digest"]
    assert len(set(seen.values())) == len(seen)
    _rule(c, rid="same")
    assert c.get_rule("same")["digest"] == base


@pytest.mark.parametrize("url", [
    "http://status.example.com/x", "status.example.com/x", "https://user:pw@status.example.com/x", "https://status.example.com:8443/x",
    "https://localhost/x", "https://box.local/x", "https://10.0.0.1/x", "https://-bad.example.com/x", "https://exa mple.com/x", "https://example.com/a'b",
])
def test_a_fact_url_must_be_a_public_https_address(direct_vm, direct_deploy, direct_alice, url):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert():
        _rule(c, url=url)
    assert c.get_count() == 0


@pytest.mark.parametrize("kw,why", [
    ({"path": ""}, "path"), ({"path": "a..b"}, "path"), ({"path": ".a"}, "path"), ({"path": "a b"}, "path"), ({"path": "a/b"}, "path"),
    ({"path": "a" * 41}, "path"), ({"path": "a." * 50 + "b"}, "path"),
    ({"value": ""}, "value must be"), ({"value": "x" * 201}, "value must be"),
    ({"fact_age": 59}, "fact_max_age must be"), ({"fact_age": 30 * 86400 + 1}, "fact_max_age must be"), ({"fact_age": 0}, "fact_max_age must be"),
])
def test_the_fact_terms_are_checked_before_anything_is_stored(direct_vm, direct_deploy, direct_alice, kw, why):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert(why):
        _rule(c, **kw)
    assert c.get_count() == 0


@pytest.mark.parametrize("url,why", [
    ("https://status.example.com/a b", "forbidden character"), ("https://status.example.com/a\tb", "forbidden character"), ("https://status.example.com/a\nb", "forbidden character"),
    ("https://user@status.example.com/x", "credentials or a port"), ("https://status.example.com:8443/x", "credentials or a port"),
    ("https://example/x", "public domain name"), ("https://a..example.com/x", "invalid url host"), ("https://" + "a" * 64 + ".example.com/x", "invalid url host"),
    ("https://exa_mple.com/x", "invalid url host"), ("https://exa%6Dple.com/x", "invalid url host"),
])
def test_a_bad_fact_url_is_refused_for_the_right_reason(direct_vm, direct_deploy, direct_alice, url, why):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert(why):
        _rule(c, url=url)
    assert c.get_count() == 0


def test_the_fact_age_may_be_exactly_a_minute_or_thirty_days(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    _rule(c, rid="lo", fact_age=60)
    _rule(c, rid="hi", fact_age=30 * 86400)
    assert c.get_count() == 2


@pytest.mark.parametrize("terms", [(5, "a", "x", 100), (API, 5, "x", 100), (API, "a", 5, 100), (API, "a", "x", "100"), (API, "a", "x", True), (None, "", "", 0)])
def test_fact_terms_must_have_the_right_types(direct_vm, direct_deploy, direct_alice, terms):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("fact terms must be"):
        c.create_rule_with_fact("t1", TARGET, "get_latest", '["m1"]', "exists", "", "", 0, "", 0, *terms)
    assert c.get_count() == 0


def test_an_empty_url_with_other_fact_terms_is_refused_and_all_empty_is_a_plain_rule(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("need a url"):
        c.create_rule_with_fact("e1", TARGET, "get_latest", '["m1"]', "exists", "", "", 0, "", 0, "", "a", "", 0)
    for extra in (("", "a", "", 0), ("", "", "x", 0), ("", "", "", 60)):
        with direct_vm.expect_revert("need a url"):
            c.create_rule_with_fact("e2", TARGET, "get_latest", '["m1"]', "exists", "", "", 0, "", 0, *extra)
    c.create_rule_with_fact("e3", TARGET, "get_latest", '["m1"]', "exists", "", "", 0, "", 0, "", "", "", 0)
    assert c.get_rule("e3")["fact"]["url"] == ""


def test_a_url_fragment_is_dropped_and_three_hundred_characters_is_the_limit(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    _rule(c, rid="frag", url=API + "#top")
    assert c.get_rule("frag")["fact"]["url"] == API
    base = "https://status.example.com/"
    ok = base + "a" * (300 - len(base))
    _rule(c, rid="long", url=ok)
    with direct_vm.expect_revert("at most 300"):
        _rule(c, rid="toolong", url=ok + "a")


def test_the_rest_of_the_rule_is_still_checked_for_a_rule_with_a_fact(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("method"):
        c.create_rule_with_fact("x", TARGET, "_private", '["m1"]', "exists", "", "", 0, "", 0, API, "status", "ok", FACT_AGE)
    with direct_vm.expect_revert("at least one condition"):
        c.create_rule_with_fact("x", TARGET, "get_latest", '["m1"]', "", "", "", 0, "", 0, API, "status", "ok", FACT_AGE)
    with direct_vm.expect_revert("already exists"):
        _rule(c)
        _rule(c)


# ---------------------------------------------------------------------------------------------------------------- observing and checking


def test_a_rule_with_a_fact_is_closed_until_the_page_has_been_observed(F):
    vm, c, _ = F
    v = c.check("f1", T0_TS)
    assert (v["open"], v["code"]) == (False, "no_observation")


def test_a_met_observation_opens_the_rule_and_records_when(F, direct_charlie):
    vm, c, fake = F
    vm.sender = direct_charlie  # observe is permissionless
    c.observe("f1")
    f = c.get_rule("f1")["fact"]
    assert (f["observed_at"], f["met"], f["observations"]) == (T0_TS, True, 1)
    assert c.check("f1", T0_TS)["open"] is True
    assert fake.calls and all(x["state"] == 1 for x in fake.calls)  # the target is still read in finalized state


def test_an_unmet_observation_keeps_the_rule_closed(F):
    vm, c, _ = F
    _serve(vm, DOWN)
    c.observe("f1")
    v = c.check("f1", T0_TS)
    assert (v["open"], v["code"]) == (False, "observation_not_met")
    assert c.get_rule("f1")["fact"]["observations"] == 1


def test_an_observation_goes_stale_after_the_rule_allows(F):
    vm, c, _ = F
    c.observe("f1")
    assert c.check("f1", T0_TS + FACT_AGE)["open"] is True  # exactly the allowed age
    v = c.check("f1", T0_TS + FACT_AGE + 1)
    assert (v["open"], v["code"]) == (False, "observation_stale")
    assert v["age"] == FACT_AGE + 1


def test_an_observation_from_the_future_of_the_callers_clock_is_refused(F):
    vm, c, _ = F
    vm.warp("2026-01-01T00:10:00Z")
    c.observe("f1")
    v = c.check("f1", T0_TS)  # the caller's clock is behind the observation
    assert (v["open"], v["code"], v["field"]) == (False, "future", "observation")


def test_the_target_is_judged_first_so_a_bad_target_closes_the_rule_whatever_the_page_says(F):
    vm, c, fake = F
    c.observe("f1")
    fake.answer["covered"] = False
    assert c.check("f1", T0_TS)["code"] == "not_true"
    fake.answer["covered"] = True
    fake.fail = True
    assert c.check("f1", T0_TS)["code"] == "unreadable"


def test_a_later_observation_can_close_a_rule_that_was_open(F):
    vm, c, _ = F
    c.observe("f1")
    assert c.check("f1", T0_TS)["open"] is True
    _serve(vm, DOWN)
    vm.warp("2026-01-01T00:05:00Z")
    c.observe("f1")
    assert c.check("f1", T0_TS + 300)["code"] == "observation_not_met"
    _serve(vm, OK)
    vm.warp("2026-01-01T00:10:00Z")
    c.observe("f1")
    assert c.check("f1", T0_TS + 600)["open"] is True


def test_check_pinned_works_for_a_rule_with_a_fact(F):
    vm, c, _ = F
    c.observe("f1")
    d = c.get_rule("f1")["digest"]
    assert c.check_pinned("f1", d, T0_TS)["open"] is True
    with vm.expect_revert("not the one expected"):
        c.check_pinned("f1", "0" * 64, T0_TS)


def test_record_stores_the_verdict_including_the_fact(F):
    vm, c, _ = F
    c.record("f1")
    assert c.get_rule("f1")["last_code"] == "no_observation"
    c.observe("f1")
    c.record("f1")
    r = c.get_rule("f1")
    assert (r["last_open"], r["last_code"], r["records"]) == (True, "ok", 2)


@pytest.mark.parametrize("status,body", [(404, "x"), (500, "x"), (503, ""), (200, ""), pytest.param(200, b"x" * 2_000_001, id="too-large")])
def test_an_unreadable_page_records_nothing(F, status, body):
    vm, c, _ = F
    _serve(vm, body, status=status)
    with vm.expect_revert("unreadable"):
        c.observe("f1")
    f = c.get_rule("f1")["fact"]
    assert (f["observed_at"], f["met"], f["observations"]) == (0, False, 0)


def test_a_page_that_cannot_be_reached_at_all_records_nothing(F):
    vm, c, _ = F
    vm.clear_mocks()
    with vm.expect_revert("unreadable"):
        c.observe("f1")
    assert c.get_rule("f1")["fact"]["observations"] == 0


def test_an_unreadable_look_does_not_start_the_rate_limit(F):
    vm, c, _ = F
    _serve(vm, "x", status=503)
    with vm.expect_revert("unreadable"):
        c.observe("f1")
    _serve(vm, OK)
    c.observe("f1")
    assert c.get_rule("f1")["fact"]["met"] is True


def test_observations_are_rate_limited_per_rule(F):
    vm, c, _ = F
    c.observe("f1")
    with vm.expect_revert("too recently"):
        c.observe("f1")
    vm.warp("2026-01-01T00:04:59Z")
    with vm.expect_revert("too recently"):
        c.observe("f1")
    vm.warp("2026-01-01T00:05:00Z")
    c.observe("f1")
    assert c.get_rule("f1")["fact"]["observations"] == 2


def test_rules_are_observed_independently(F):
    vm, c, _ = F
    _rule(c, rid="f2")
    c.observe("f1")
    assert c.get_rule("f1")["fact"]["observations"] == 1 and c.get_rule("f2")["fact"]["observations"] == 0
    assert c.check("f2", T0_TS)["code"] == "no_observation"


def test_an_unknown_rule_cannot_be_observed(F):
    vm, c, _ = F
    with vm.expect_revert("not found"):
        c.observe("nope")


@pytest.mark.parametrize("body,path,value,met", [
    ('{"status": "ok"}', "status", "ok", True),
    ('{"status": "OK"}', "status", "ok", False),
    ('{"a": {"b": {"c": "deep"}}}', "a.b.c", "deep", True),
    ('{"a": {"b": "x"}}', "a.b.c", "x", False),
    ('{"a": 1}', "b", "1", False),  # a key that is not there
    ('{"a": 1}', "a", "1", True),
    ('{"items": [{"id": "x"}, {"id": "y"}]}', "items.1.id", "y", True),
    ('{"items": [{"id": "x"}]}', "items.5.id", "x", False),
    ('{"items": [{"id": "x"}]}', "items.id", "x", False),
    ('{"up": true}', "up", "true", True),
    ('{"up": false}', "up", "true", False),
    ('{"n": 5}', "n", "5", True),
    ('{"n": 5.5}', "n", "5.5", True),
    ('{"n": 5}', "n", "5.0", False),
    ('{"gone": null}', "gone", "null", True),
    ('{"a": {"b": "c"}}', "a", "{'b': 'c'}", False),
    ('{"a": ["b"]}', "a", "['b']", False),
    ("not json", "status", "ok", False),
    ('{"status": "ok"} trailing', "status", "ok", False),
    ("[1]", "status", "ok", False),
])
def test_the_field_is_compared_as_exact_text(direct_vm, direct_deploy, direct_alice, body, path, value, met):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    fake = FakeTarget()
    fake.install(direct_vm)
    c = direct_deploy(CONTRACT)
    _rule(c, path=path, value=value)
    _serve(direct_vm, body)
    c.observe("f1")
    assert c.get_rule("f1")["fact"]["met"] is met
    assert direct_vm._captured_validators[-1][0] == {"kind": "read", "met": met}


# ---------------------------------------------------------------------------------------------------------------- the validator vote


def test_the_leader_proposes_what_the_page_shows(F):
    vm, c, _ = F
    c.observe("f1")
    assert _leader(vm) == {"kind": "read", "met": True}


def test_a_validator_that_reads_the_same_page_accepts(F):
    vm, c, _ = F
    c.observe("f1")
    assert vm.run_validator() is True


def test_a_validator_that_sees_a_different_page_rejects_a_met_claim(F):
    """The attack the vote exists to stop: a leader that says the fact holds while the page says otherwise."""
    vm, c, _ = F
    c.observe("f1")
    _serve(vm, DOWN)
    assert vm.run_validator() is False
    assert vm.run_validator(leader_result={"kind": "read", "met": True}) is False
    assert vm.run_validator(leader_result={"kind": "read", "met": False}) is True  # and it accepts the leader that tells the truth


def test_a_validator_that_sees_the_fact_met_rejects_an_unmet_claim(F):
    vm, c, _ = F
    _serve(vm, DOWN)
    c.observe("f1")
    assert _leader(vm) == {"kind": "read", "met": False}
    _serve(vm, OK)
    assert vm.run_validator() is False


@pytest.mark.parametrize("status,body", [(404, "x"), (500, "x"), (200, ""), pytest.param(200, b"x" * 2_000_001, id="too-large")])
def test_a_validator_that_cannot_read_the_page_rejects_a_read_claim(F, status, body):
    vm, c, _ = F
    c.observe("f1")
    _serve(vm, body, status=status)
    assert vm.run_validator() is False


def test_validators_agree_that_an_unreadable_page_is_unreadable(F):
    vm, c, _ = F
    _serve(vm, "x", status=404)
    with vm.expect_revert("unreadable"):
        c.observe("f1")
    assert _leader(vm) == {"kind": "unreadable", "met": False}
    assert vm.run_validator() is True
    _serve(vm, OK)
    assert vm.run_validator() is False


def test_a_validator_rejects_an_unreadable_claim_even_when_the_page_it_reads_is_also_unmet(F):
    vm, c, _ = F
    _serve(vm, "x", status=404)
    with vm.expect_revert("unreadable"):
        c.observe("f1")
    _serve(vm, DOWN)  # readable, fact not met: same 'met', different 'readable'
    assert vm.run_validator() is False


@pytest.mark.parametrize("field,value", [("met", False), ("kind", "unreadable"), ("kind", "other")])
def test_a_validator_rejects_a_leader_result_with_any_field_altered(F, field, value):
    vm, c, _ = F
    c.observe("f1")
    forged = _leader(vm)
    forged[field] = value
    assert vm.run_validator(leader_result=forged) is False


@pytest.mark.parametrize("junk", [None, [], "read", 5, True, {}, {"kind": "read"}, {"met": True}, {"kind": 1, "met": True}, {"kind": "read", "met": "yes"}, {"kind": "read", "met": 1}])
def test_a_validator_rejects_a_leader_result_of_the_wrong_shape(F, junk):
    vm, c, _ = F
    c.observe("f1")
    assert vm.run_validator(leader_result=junk) is False


def test_a_validator_rejects_a_leader_that_errored(F):
    vm, c, _ = F
    c.observe("f1")
    assert vm.run_validator(leader_error=Exception("the leader crashed")) is False
