# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from datetime import datetime, timezone
from genlayer import *
from genlayer.py.public_abi import StorageType


def _bad(c: bool, m: str) -> None:
    if c:
        raise gl.vm.UserError(m)


class SluiceConsumer(gl.Contract):
    """The smallest consumer of Sluice, used by the live run. perform() records a note only while the rule it is pinned to is open in
    Sluice's answer, which Sluice builds from the target's FINALIZED state. It passes its own clock and the rule hash it expects."""

    sluice: Address
    rule_id: str
    rule_digest: str
    performed: u256
    last_note: str

    def __init__(self, sluice: str, rule_id: str, rule_digest: str):
        self.sluice = sluice if isinstance(sluice, Address) else Address(sluice)
        self.rule_id = rule_id
        self.rule_digest = rule_digest

    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    @gl.public.write
    def perform(self, note: str) -> None:
        _bad(not isinstance(note, str) or not 0 < len(note) <= 200, "note must be 1 to 200 characters")
        v = gl.get_contract_at(self.sluice).view(state=StorageType.LATEST_FINAL).check_pinned(self.rule_id, self.rule_digest, self._now())
        _bad(not isinstance(v, dict) or type(v.get("open")) is not bool or not isinstance(v.get("code"), str), "Sluice answered in a shape this contract does not accept")
        _bad(not v["open"], "Refused: " + v["code"] + " " + str(v.get("field", "")))
        self.performed = self.performed + 1
        self.last_note = note

    @gl.public.view
    def get_state(self) -> dict:
        return {"sluice": self.sluice.as_hex, "rule_id": self.rule_id, "rule_digest": self.rule_digest, "performed": self.performed, "last_note": self.last_note}
