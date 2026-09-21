"""Bounded API inputs. Identity is always taken from the bearer credential."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[
    str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
Arg = Annotated[str, Field(min_length=1, max_length=8192)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GPU(Input):
    id: Identifier
    model: ShortText
    memory_mib: int = Field(gt=0, le=2**30)


class NodeCreate(Input):
    id: Identifier
    cpus: int = Field(gt=0, le=65536)
    memory_mib: int = Field(gt=0, le=2**40)
    gpus: list[GPU] = Field(default_factory=list, max_length=128)

    @field_validator("gpus")
    @classmethod
    def unique_gpu_ids(cls, value):
        if len({gpu.id for gpu in value}) != len(value):
            raise ValueError("GPU IDs must be unique on a node")
        return value


class UserIdentity(Input):
    uid: int = Field(ge=0, le=2**32 - 1)
    gid: int = Field(ge=0, le=2**32 - 1)


class UserCreate(Input):
    id: Identifier
    allowed_nodes: list[Identifier] = Field(default_factory=list, max_length=256)
    identity: UserIdentity | None = None


class Grants(Input):
    allowed_nodes: list[Identifier] = Field(max_length=256)


class EnvironmentCreate(Input):
    name: ShortText
    node_id: Identifier
    ssh_target: str = Field(min_length=1, max_length=512)
    workdir: str = Field(min_length=1, max_length=1024)

    @field_validator("workdir")
    @classmethod
    def absolute_path(cls, value):
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("workdir must be an absolute container path")
        return value


class EnvironmentVerification(Input):
    challenge: str = Field(min_length=20, max_length=256)
    container_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    uid: int = Field(ge=0, le=2**32 - 1)
    gid: int = Field(ge=0, le=2**32 - 1)
    cpus: int = Field(gt=0, le=65536)
    memory_mib: int = Field(gt=0, le=2**40)
    gpu_ids: list[Identifier] = Field(default_factory=list, max_length=128)


class EnvironmentObservation(Input):
    environment_id: Identifier
    container_id: str = Field(min_length=1, max_length=128)
    ready: bool
    gpu_ids: list[Identifier] = Field(default_factory=list, max_length=128)


class Heartbeat(Input):
    environments: list[EnvironmentObservation] = Field(default_factory=list, max_length=1024)


class JobSpec(Input):
    name: ShortText
    environment_ids: list[Identifier] = Field(min_length=1, max_length=32)
    argv: list[Arg] = Field(min_length=1, max_length=128)
    cpus: int = Field(gt=0, le=65536)
    memory_mib: int = Field(gt=0, le=2**40)
    gpu_count: int = Field(default=0, ge=0, le=128)
    gpu_model: ShortText | None = None
    gpu_min_memory_mib: int = Field(default=0, ge=0, le=2**30)
    workdir: str | None = Field(default=None, min_length=1, max_length=1024)

    @field_validator("workdir")
    @classmethod
    def absolute_workdir(cls, value):
        if value is not None and (not value.startswith("/") or "\x00" in value):
            raise ValueError("workdir must be an absolute path")
        return value

    @model_validator(mode="after")
    def resource_consistency(self):
        if not self.gpu_count and (self.gpu_model or self.gpu_min_memory_mib):
            raise ValueError("GPU requirements need gpu_count > 0")
        if len(self.environment_ids) != len(set(self.environment_ids)):
            raise ValueError("environment_ids must be unique")
        if any("\x00" in arg for arg in self.argv):
            raise ValueError("argv cannot contain NUL")
        if sum(len(arg.encode()) for arg in self.argv) > 32768:
            raise ValueError("argv must fit within 32 KiB")
        return self


class PlanRequest(Input):
    primary: JobSpec
    alternatives: list[JobSpec] = Field(default_factory=list, max_length=8)


class JobSubmit(Input):
    request_key: str = Field(min_length=8, max_length=128)
    mode: Literal["queue_if_unavailable", "start_if_available"] = "queue_if_unavailable"
    spec: JobSpec


class WorkerEvent(Input):
    event_id: Identifier
    execution_id: Identifier
    kind: Literal["started", "succeeded", "failed", "cancelled", "not_started"]
    occurred_at: float = Field(ge=0, le=253402300799, allow_inf_nan=False)
    exit_code: int | None = Field(default=None, ge=-255, le=255)
    log_path: str | None = Field(default=None, max_length=1024)
    result_path: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def valid_exit(self):
        if self.kind == "succeeded" and self.exit_code != 0:
            raise ValueError("succeeded requires exit_code=0")
        if self.kind == "started" and self.exit_code is not None:
            raise ValueError("started has no exit code")
        if self.kind == "failed" and self.exit_code == 0:
            raise ValueError("failed cannot have exit_code=0")
        return self


class LocalEnvironment(EnvironmentCreate):
    instance_id: Identifier
    uid: int = Field(ge=0, le=2**32 - 1)
    gid: int = Field(ge=0, le=2**32 - 1)
    cpus: int = Field(gt=0, le=65536)
    memory_mib: int = Field(gt=0, le=2**40)
    gpu_ids: list[Identifier] = Field(default_factory=list, max_length=128)


class RunnerIdentity(Input):
    instance_id: Identifier
    runner_id: Identifier
    uid: int = Field(ge=0, le=2**32 - 1)
    gid: int = Field(ge=0, le=2**32 - 1)
    claim_id: Identifier | None = None


class LocalSubmit(JobSubmit):
    runner: RunnerIdentity


class LocalEvent(Input):
    runner: RunnerIdentity
    event: WorkerEvent


class NotificationSet(Input):
    secret_ref: Identifier
    channel_id: str = Field(pattern=r"^\d{1,30}$")


class NotificationProvision(Input):
    user_id: Identifier
    secret_ref: Identifier
    channel_id: str = Field(pattern=r"^\d{1,30}$")


class Error(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)
