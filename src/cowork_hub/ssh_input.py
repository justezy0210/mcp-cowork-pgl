"""Parse a connection command as data; never execute shell input from the web."""

import re
import shlex

from pydantic import Field, ValidationError

from .models import Error, Identifier, Input
from .ssh_runner import SSHTarget


class SSHCommand(Input):
    ssh_command: str = Field(min_length=1, max_length=512)


class ContainerConnect(SSHCommand):
    connector_id: Identifier
    node_id: Identifier
    request_key: str = Field(min_length=8, max_length=128)


def parse_ssh_command(command):
    try:
        if re.search(r"[\x00-\x1f\x7f]", command):
            raise ValueError
        parts = shlex.split(command)
        if not parts or parts.pop(0) != "ssh":
            raise ValueError
        destination, port = None, None
        while parts:
            part = parts.pop(0)
            if part == "-p":
                if port is not None:
                    raise ValueError
                port = int(parts.pop(0))
            elif part.startswith("-p"):
                if port is not None:
                    raise ValueError
                port = int(part[2:])
            elif part.startswith("-") or destination is not None:
                raise ValueError
            else:
                destination = part
        if not destination or destination.count("@") != 1:
            raise ValueError
        user, host = destination.split("@")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return SSHTarget(user=user, host=host, port=22 if port is None else port)
    except (ValueError, IndexError, ValidationError):
        raise Error(
            "INVALID_SSH_COMMAND",
            "Use ssh -p PORT USER@HOST without other options or commands",
            422,
        ) from None
