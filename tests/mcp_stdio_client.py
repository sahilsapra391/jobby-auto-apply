"""A minimal MCP stdio client, for tests that drive the firewall directly.

Small on purpose. The point of the end-to-end test is that nothing between the
client and the page is a stub, so this speaks the real protocol over the real
pipes and does nothing else.
"""

import json
import os
import queue
import subprocess
import threading


class Client:
    def __init__(self, config, timeout=90):
        environment = dict(os.environ)
        environment.update(config.get("env") or {})
        self.timeout = timeout
        self.process = subprocess.Popen(
            [config["command"]] + list(config.get("args") or []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=environment, bufsize=1,
        )
        self.replies = queue.Queue()
        self.errors = []
        threading.Thread(target=self._read_replies, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()
        self._next_id = 0
        self.call("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "jobby-tests", "version": "1"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read_replies(self):
        for line in self.process.stdout:
            line = line.strip()
            if line.startswith("{"):
                try:
                    self.replies.put(json.loads(line))
                except json.JSONDecodeError:
                    pass

    def _read_errors(self):
        for line in self.process.stderr:
            self.errors.append(line.rstrip())

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, method, params=None):
        self._next_id += 1
        identifier = self._next_id
        self._send({"jsonrpc": "2.0", "id": identifier, "method": method,
                    **({"params": params} if params else {})})
        while True:
            reply = self.replies.get(timeout=self.timeout)
            if reply.get("id") == identifier:
                return reply

    def tool(self, name, arguments):
        return self.call("tools/call", {"name": name, "arguments": arguments})

    def finish(self):
        """Close stdin the way a finishing provider CLI does, and wait."""
        self.process.stdin.close()
        self.process.wait(timeout=self.timeout)

    def close(self):
        try:
            self.process.terminate()
            self.process.wait(timeout=10)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
