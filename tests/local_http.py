"""The HTTP server every test serves from on a local port (loop#508).

http.server.HTTPServer.server_bind sets server_name with socket.getfqdn(host), a reverse DNS lookup of the bound
address that no test reads. On some hosts that lookup takes 35 s per server (a Homebrew Python on macOS in a launchd
Background session), so a test's servers, and the ledger console a stub checker starts on each call, wait on the
host's resolver. LocalHTTPServer binds the same way and names the server by its bound host instead.
"""
import socketserver
from http.server import ThreadingHTTPServer


class LocalHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the reverse DNS lookup when it binds."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
