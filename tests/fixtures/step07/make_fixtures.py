#!/usr/bin/env python3
"""Make step 07's test fixtures, once. Needs openssl and the pinned capsulectl (steps/07-ask.setup.sh).

Usage: python3 -B tests/fixtures/step07/make_fixtures.py

alice and bob (the rehearsal population's TEST keys) join once; then bob asks
alice through step 07's own ask code, in five runs. Each is a findings
directory for steps/07-ask's judge: bob/asks/alice/ (the asks, the carrier log
and each outcome bundle) and alice/advertised/ (her signed commitment and
policy). The planted failures are made by an asker or a responder that
misbehaves while the ask runs, never by editing bytes afterwards:

  good/                  exchange (a signed refusal), history_card (an
                         artifact), exchange_late (a recorded absence)
  sealed-after-send/     the carrier sends the request bytes, and only then
                         does bob seal them in his book
  stranger-safe-refused/ alice refuses history_card/1, which her commitment
                         lists as answered to anyone, with a refusal signed by
                         her key
  wrong-key/             an exchange answer signed by a key other than
                         alice's listed key, recorded under that key
  late-counted/          the late reply is recorded as an answer after the
                         deadline instead of as an absence

The runs share one wait for their deadlines (alice answers within PT1M), so
this takes a little over a minute.
"""
import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step07_ask", str(ROOT / "steps" / "07-ask"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

ASKS = step.ASKS


def signed_refusal(ask, reason, key_pem):
    """A refusal of ask's request, signed by key_pem (the profile's refusal: JCS of request_digest, reason,
    issued_at)."""
    body = {"request_digest": step.sha256((ask.dir / "request.json").read_bytes()), "reason": reason,
            "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    signing = ask.dir.parent / "refusal.signing-input"
    signing.write_bytes(check_commitment.canonical(body))
    sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key_pem), "-rawin", "-in", str(signing)],
                         check=True, capture_output=True).stdout.hex()
    signing.unlink()
    pub = subprocess.run(["openssl", "pkey", "-in", str(key_pem), "-pubout", "-outform", "DER"], check=True,
                         capture_output=True).stdout[-32:].hex()
    return pub, check_commitment.canonical({**body, "key_id": pub, "sig": sig})


class SendsFirst(step.Ask):
    """The carrier sends the request bytes before bob seals them (with the same nonce, so the same bytes)."""

    def request_map(self):
        return {**super().request_map(), "profile": "evidence-interop/0", "nonce": "07" * 16}

    def seal(self):
        (self.dir / "request.json").write_bytes(check_commitment.canonical(self.request_map()))
        self.send()
        time.sleep(1.1)
        sent = (self.dir / "request.json").read_bytes()
        (self.dir / "request.json").unlink()  # capsulectl writes its own copy, and never over a file
        super().seal()
        assert (self.dir / "request.json").read_bytes() == sent, "the sealed bytes differ from the sent ones"

    def send(self):
        if self.sent_at is None:
            super().send()


class RefusesPublic(step.Ask):
    """alice refuses a stranger-safe request, signing the refusal with her own (listed) TEST key."""

    def respond(self):
        _, self.reply = signed_refusal(self, "not_authorized", ROOT / self.asked_key_dir / "node-key.pem")
        (self.dir / "answer.json").write_bytes(self.reply)


class Impostor(step.Ask):
    """Someone other than alice answers, with a key of its own, and bob records the answer under that key."""

    def respond(self):
        self.impostor_key, self.reply = signed_refusal(self, "not_authorized", self.impostor_pem)
        (self.dir / "answer.json").write_bytes(self.reply)

    def record_response(self):
        r = self.asker.cap("book", "request", "--for", self.record["record_id"],
                           "--response", str(self.dir / "answer.json"),
                           "--responder-key", self.impostor_key, "--responder-checkpoint-key",
                           self.asked.checkpoint_key)
        self.outcome = step.keep(r.stdout)


class CountsLate(step.Ask):
    """The reply that arrives after the deadline is recorded as an answer."""

    def after_deadline(self):
        self.deliver()
        self.record_response()
        self.disclose()


def main():
    capsulectl = step.tools()
    if not capsulectl.is_file():
        raise SystemExit(f"no capsulectl at {capsulectl}: run steps/07-ask.setup.sh")
    pop = json.loads((ROOT / "population" / "rehearsal" / "population.json").read_text())
    with tempfile.TemporaryDirectory(prefix="step07-fixtures-") as tmp:
        tmp = Path(tmp)
        base = tmp / "joined"
        nodes = {n["name"]: step.join(n, tmp, capsulectl, base) for n in pop["nodes"]}
        bob, alice = nodes["bob"], nodes["alice"]
        RefusesPublic.asked_key_dir = next(n["key_dir"] for n in pop["nodes"] if n["name"] == "alice")
        Impostor.impostor_pem = tmp / "impostor-key.pem"
        step12_key = step.step12.make_test_controller_key
        step12_key(Impostor.impostor_pem)  # a fresh Ed25519 TEST key, not alice's
        runs = {
            "good": {name: (spec, step.Ask) for name, spec in ASKS.items()},
            "sealed-after-send": {"exchange": (ASKS["exchange"], SendsFirst)},
            "stranger-safe-refused": {"history_card": (ASKS["history_card"], RefusesPublic)},
            "wrong-key": {"exchange": (ASKS["exchange"], Impostor)},
            "late-counted": {"exchange_late": (ASKS["exchange_late"], CountsLate)},
        }
        for name in runs:
            out = tmp / "out" / name
            shutil.copytree(base / "alice", out / "alice")
        step.run_asks([(tmp / "out" / name, bob, alice, asks) for name, asks in runs.items()])
        for name in runs:
            dest = HERE / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(tmp / "out" / name, dest)
            print(f"wrote {dest.relative_to(ROOT)}", file=sys.stderr)
    (HERE / "bob.key_id").write_text(bob.key + "\n")


if __name__ == "__main__":
    main()
