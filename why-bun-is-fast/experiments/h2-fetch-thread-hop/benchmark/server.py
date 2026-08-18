#!/usr/bin/env python3
"""
H2 target server — a single, fixed, runtime-neutral HTTP server (Python
stdlib http.server) used identically as the target for all three fetch()
clients (Bun, Node, Deno). Per Stage 12 Section 14/H2 design: the server
must never become a hidden variable, so it is not implemented in any of
the three runtimes under test.

Routes:
  GET /          -> fixed JSON body, default HTTP/1.1 persistent connection
                     (keep-alive). Used for the KEEP-ALIVE / steady-state
                     phase.
  GET /cold      -> identical body, but responds with `Connection: close`,
                     forcing the client to close and re-establish a fresh
                     TCP connection for its next request. This is standard
                     HTTP/1.1 protocol behavior (not a client-side hack),
                     so all three fetch() implementations should honor it
                     identically. Used for the COLD-CONNECTION phase.
  GET /stats     -> {"connections": N, "requests": M, "cold_requests": C,
                     "keepalive_requests": K} — for sanity-checking actual
                     connection-reuse behavior between phases. Not counted
                     towards the other counters itself.
  POST /reset    -> resets all counters to 0 (used at phase boundaries).

Response body is fixed: b'{"ok":true}' (11 bytes), Content-Type
application/json, Content-Length always 11.
"""
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BODY = b'{"ok":true}'
assert len(BODY) == 11

_lock = threading.Lock()
_stats = {"connections": 0, "requests": 0, "cold_requests": 0, "keepalive_requests": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # enables persistent connections by default

    def setup(self):
        super().setup()
        # Disable Nagle's algorithm: without this, small HTTP request/response
        # packets can interact with delayed-ACK timers and introduce ~40ms
        # artificial latency spikes unrelated to anything this experiment is
        # trying to measure. This must be set identically for every client
        # comparison (it affects all three runtimes equally, since it's a
        # server-socket-level setting, not a per-runtime one).
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with _lock:
            _stats["connections"] += 1

    def log_message(self, format, *args):
        pass  # silence per-request logging — avoid I/O overhead affecting timing

    def _send_fixed_body(self, close):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(BODY)

    def do_GET(self):
        if self.path == "/stats":
            import json
            with _lock:
                payload = json.dumps(dict(_stats)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        with _lock:
            _stats["requests"] += 1
            if self.path == "/cold":
                _stats["cold_requests"] += 1
            else:
                _stats["keepalive_requests"] += 1

        self._send_fixed_body(close=(self.path == "/cold"))

    def do_POST(self):
        if self.path == "/reset":
            with _lock:
                for k in _stats:
                    _stats[k] = 0
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"H2 server listening on 127.0.0.1:{port}", flush=True)
    server.serve_forever()
