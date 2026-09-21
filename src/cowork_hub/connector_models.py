"""Registration requests contain SSH metadata, never arbitrary commands or credentials."""

from typing import Literal

from pydantic import Field, model_validator

from .models import Identifier, Input, ShortText
from .ssh_runner import SSHRegistration


class TokenCreate(Input):
    name: ShortText


class ConnectorCreate(Input):
    instance_id: Identifier
    name: ShortText
    environment_id: Identifier


class ConnectorPoll(Input):
    instance_id: Identifier


class RegistrationCreate(Input):
    request_key: str = Field(min_length=8, max_length=128)
    connector_id: Identifier
    target: SSHRegistration


class RegistrationResult(Input):
    instance_id: Identifier
    claim_id: Identifier
    environment_id: Identifier | None = None
    error_code: (
        Literal[
            "SSH_RESULT_UNCERTAIN",
            "SSH_RUNNER_ERROR",
            "FORBIDDEN_NODE",
            "IDENTITY_MISMATCH",
            "SSH_GPU_NODE_MISMATCH",
            "SSH_GPU_PROBE_FAILED",
            "SSH_WORKDIR_MISSING",
            "SSH_SHARED_PATH_REQUIRED",
            "SSH_ROUTE_CONFLICT",
            "RUNNER_CONFIG_MISMATCH",
            "HUB_UNREACHABLE",
            "HUB_TEMPORARILY_UNAVAILABLE",
            "SSH_CONFIG_PERMISSIONS",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def one_result(self):
        if (self.environment_id is None) == (self.error_code is None):
            raise ValueError("Supply exactly one registration outcome")
        return self
