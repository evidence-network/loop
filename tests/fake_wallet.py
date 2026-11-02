"""Two fake lab wallets (lab-1 pays, lab-2 is paid) on local ports, speaking the node's `/api/wallet` JSON as
mesh-llm's control API does, for the Goose payment tests (loop#349). No money moves; the preimages are random."""
import hashlib
import http.server
import json
import os
import threading


class FakeLab:
    def __init__(self, spendable=9873, spent_today=0, send_result="succeeded", fee=5, pending=False):
        self.spendable, self.spent_today, self.send_result, self.fee, self.pending = spendable, spent_today, send_result, fee, pending
        self.txs = {"lab-1": [], "lab-2": []}
        self.invoices = {}
        self.sent = 0
        self.servers = {}
        self.calls = []

    def handler(self, who):
        lab = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                cmd = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                lab.calls.append((who, cmd["command"]))
                code, body = 200, None
                c = cmd["command"]
                if c == "balance":
                    body = {"available_for_inference_msat": 0, "spendable_msat": lab.spendable}
                elif c == "policy":
                    body = {"daily_budget_msat": 9000, "mode": "automatic", "spent_today_msat": lab.spent_today,
                            "remaining_daily_budget_msat": 9000 - lab.spent_today}
                elif c == "transactions":
                    body = list(lab.txs[who]) + ([{"status": "pending", "inbound": False, "payment_hash": "0" * 64}] if lab.pending else [])
                elif c == "fund":
                    pre = os.urandom(32)
                    h = hashlib.sha256(pre).hexdigest()
                    lab.invoices[h] = (pre.hex(), cmd["amount_msat"], who)
                    body = {"bolt11": "lnbc" + h[:40] + "x" * 30, "payment_hash": h, "amount_msat": cmd["amount_msat"],
                            "payee": "02" + "ab" * 32, "expires_at_ms": 4102444800000}
                elif c == "send":
                    h = next((h for h, v in lab.invoices.items() if cmd["invoice"] == "lnbc" + h[:40] + "x" * 30), None)
                    if h is None:
                        code, body = 400, {"error": "unknown invoice"}
                    else:
                        lab.sent += 1
                        pre, amount, payee = lab.invoices[h]
                        lab.txs[who].append({"id": "1", "payment_hash": h, "inbound": False, "amount_msat": amount, "fee_msat": 0,
                                             "status": lab.send_result, "preimage": pre, "status_msg": "x"})
                        lab.txs[payee].append({"id": "2", "payment_hash": h, "inbound": True, "amount_msat": amount - lab.fee,
                                               "fee_msat": lab.fee, "status": lab.send_result, "preimage": pre})
                        body = {"payment_hash": h}
                else:
                    code, body = 400, {"error": "unknown command"}
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        return H

    def __enter__(self):
        for who in ("lab-1", "lab-2"):
            s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.handler(who))
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.servers[who] = s
        self.ports = {w: s.server_address[1] for w, s in self.servers.items()}
        return self

    def __exit__(self, *a):
        for s in self.servers.values():
            s.shutdown()
            s.server_close()
