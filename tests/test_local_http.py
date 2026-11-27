"""tests/local_http.py: the tests' HTTP servers bind without a reverse DNS lookup (loop#508)."""
import re
import socket
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from local_http import LocalHTTPServer  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("content-length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class LocalHTTPServerTest(unittest.TestCase):
    def test_it_binds_and_answers_without_asking_the_resolver(self):
        with mock.patch.object(socket, "getfqdn", side_effect=AssertionError("getfqdn called")), \
                mock.patch.object(socket, "gethostbyaddr", side_effect=AssertionError("gethostbyaddr called")):
            server = LocalHTTPServer(("127.0.0.1", 0), Handler)
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            port = server.server_address[1]
            self.assertEqual((server.server_name, server.server_port), ("127.0.0.1", port))
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
                self.assertEqual(r.read(), b"ok")


class EveryTestServerIsLocalTest(unittest.TestCase):
    def test_no_test_builds_an_http_server_that_looks_up_its_name(self):
        """http.server's own server classes call getfqdn when they bind; tests build LocalHTTPServer instead."""
        direct = re.compile(r"\b(?:Threading)?HTTPServer\s*\(")
        found = []
        for p in sorted((ROOT / "tests").rglob("*.py")):
            if p.name in ("local_http.py", Path(__file__).name) or "fixtures" in p.parts:
                continue
            for n, line in enumerate(p.read_text().splitlines(), 1):
                if direct.search(line):
                    found.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
