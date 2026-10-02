#!/usr/bin/env python3
"""Forced SSH command: forward one bounded web API request to a fixed hub.

Install a copy outside the checkout and use an authorized_keys `restrict,command=`
entry. Never run SSH_ORIGINAL_COMMAND or accept a target address from stdin.
Only Python's standard library is required on the SSH host.
"""

import argparse
import base64
import http.client
import json
import re
import signal
import sys

MAX_BODY = 64 * 1024
MAX_INPUT = 128 * 1024
MAX_RESPONSE = 4 * 1024 * 1024


def forward(payload, host, port):
    method, path = payload.get("method"), payload.get("path", "")
    authorization = payload.get("authorization", "")
    if (
        method not in ("GET", "POST", "DELETE")
        or not isinstance(path, str)
        or len(path) > 4096
        or not re.fullmatch(r"/v1/web/[A-Za-z0-9_./-]+", path.split("?")[0])
        or any(part in (".", "..") for part in path.split("?")[0].split("/"))
        or "//" in path.split("?")[0]
        or re.search(r"[\x00-\x20\x7f#\\]", path)
        or not isinstance(authorization, str)
        or len(authorization) > 16384
        or not re.fullmatch(r"Bearer [A-Za-z0-9_.-]+", authorization)
    ):
        raise ValueError("INVALID_REQUEST")
    body = base64.b64decode(payload.get("body", ""), validate=True)
    if len(body) > MAX_BODY or (body and method != "POST"):
        raise ValueError("INVALID_REQUEST")
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request(method, path, body=body, headers={
            "Authorization": authorization,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
        })
        response = connection.getresponse()
        data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE or response.getheader("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("INVALID_RESPONSE")
        return {"status": response.status, "body": base64.b64encode(data).decode("ascii")}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    # Covers stalled stdin and responses as well as individual socket operations.
    signal.alarm(12)
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("REQUEST_TOO_LARGE")
        result = forward(json.loads(raw), args.host, args.port)
    except Exception:
        result = {"status": 502, "body": base64.b64encode(b'{"error":{"code":"HUB_UNAVAILABLE"}}').decode("ascii")}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
