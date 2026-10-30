"""Ask the bot's Chrome to exit through DevTools so it flushes the X login to disk."""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import urllib.request
from urllib.parse import urlparse


def browser_close(port: int) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
        info = json.loads(response.read().decode())
    ws_url = info.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        raise RuntimeError("Chrome did not advertise a browser websocket")
    _send(ws_url, json.dumps({"id": 1, "method": "Browser.close"}))


def _send(ws_url: str, payload: str) -> None:
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    with socket.create_connection((host, port), timeout=5) as sock:
        sock.sendall(request.encode())
        deadline_chunks = []
        while b"\r\n\r\n" not in b"".join(deadline_chunks):
            chunk = sock.recv(4096)
            if not chunk:
                break
            deadline_chunks.append(chunk)
        handshake = b"".join(deadline_chunks)
        if b" 101 " not in handshake.split(b"\r\n", 1)[0]:
            raise RuntimeError("Chrome websocket upgrade failed")
        data = payload.encode()
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(length.to_bytes(8, "big"))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        sock.sendall(bytes(header) + masked)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gracefully close the bot Chrome via CDP.")
    parser.add_argument("--port", type=int, default=9222)
    args = parser.parse_args()
    browser_close(args.port)


if __name__ == "__main__":
    main()
