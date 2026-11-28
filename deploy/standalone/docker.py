#!/usr/bin/env python3
"""Manage a private standalone vault on a local Linux Docker Engine.

Only new instance initialization and fresh-target restore allocate storage. Existing
volumes are never adopted, re-owned, erased, or pruned. All engine administrators
and the operator-owned state/repository directories are in the trust boundary.
"""

import argparse
import contextlib
import fcntl
import hashlib
import hmac
import io
import json
import os
import re
import runpy
import secrets
import ssl
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HEALTHY = ("ready", "restored")
CREDENTIAL_FILES = (
    "database-url",
    "token-pepper",
    "bootstrap-tokens",
    "postgres-password",
    "tls-cert.pem",
    "tls-key.pem",
    "device-token",
)

PACKAGE = Path(__file__).resolve().parent
DB_IMAGE = (
    "pgvector/pgvector@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf"
)
GATEWAY_IMAGE = (
    "ghcr.io/nginxinc/nginx-unprivileged@sha256:"
    "8ff43bb0bd15a960d4cce6bcdfabf97c6aeb29067e3ec401f5d0eb77e66d4f8c"
)
PIN = re.compile(r"(?:sha256:|[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:)[0-9a-f]{64}")


class Failed(Exception):
    """Safe operator diagnostic, never wrap a secret-bearing subprocess error."""


def call(args: list[str], *, data: bytes | None = None, timeout: int = 300) -> bytes:
    # No shell; callers construct commands and validate resource identifiers and image pins.
    result = subprocess.run(  # noqa: S603
        args, input=data, capture_output=True, timeout=timeout, check=False
    )
    if result.returncode:
        # Tool stderr may include credentials, SQL or restored content. Never relay it.
        raise Failed(f"{Path(args[0]).name} operation failed (exit {result.returncode})")
    return result.stdout


def private_file(path: Path, data: bytes) -> None:
    with path.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_private(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + "-pending-" + secrets.token_hex(8))
    private_file(temporary, data)
    os.replace(temporary, path)
    sync_directory(path.parent)


@contextlib.contextmanager
def lifecycle_lock(path: Path, *, create: bool = False):
    """Never load mutable configuration until this stable lock inode is held."""
    path = path.absolute()
    if path.resolve() != path:
        raise Failed("Instance path must not contain symlinks")
    if create:
        path.mkdir(mode=0o700)
        sync_directory(path.parent)
    path = private_directory(path)
    fd = os.open(path / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or metadata.st_nlink != 1
        ):
            raise Failed("Unsafe lifecycle lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failed("Another lifecycle operation holds this instance lock") from None
        sync_directory(path)
        yield path
    finally:
        os.close(fd)


def private_directory(path: Path) -> Path:
    path = path.absolute()
    if path.is_symlink() or path.resolve() != path:
        raise Failed("Use an absolute path without symlink components")
    st = path.stat()
    if not path.is_dir() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Failed("Directory must belong to the operator with mode 0700")
    if any(character in str(path) for character in ",\n\r\0'"):
        raise Failed("Docker paths cannot contain commas, quotes or line breaks")
    return path


def read_private(path: Path) -> bytes:
    if path.is_symlink():
        raise Failed("Private files must not be symlinks")
    st = path.stat()
    if not path.is_file() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Failed("Private files must belong to the operator with mode 0600")
    if st.st_size > 131072:
        raise Failed("Private file exceeds its size limit")
    return path.read_bytes()


class Instance:
    def __init__(self, path: Path, config: dict):
        self.path = private_directory(path)
        self.config = config
        self.docker = ["docker", "--context", config["context"]]
        if self.engine_id() != config["engine_id"]:
            raise Failed("Docker Engine identity differs from the recorded instance")

    def engine_id(self) -> str:
        return call([*self.docker, "info", "--format", "{{.ID}}"]).decode().strip()

    def save(self) -> None:
        # Caller holds the lifecycle lock. A successful return includes directory fsync.
        self.config["configuration_generation"] = self.config.get("configuration_generation", 0) + 1
        atomic_private(self.path / "config.json", json.dumps(self.config, indent=2).encode())

    @property
    def secret_dir(self) -> Path:
        generation = self.config.get("credential_generation")
        if generation is None:
            return private_directory(self.path / "secrets")  # Reviewed legacy instance format.
        if not re.fullmatch(r"[0-9a-f]{32}", generation):
            raise Failed("Invalid credential generation")
        return private_directory(self.path / "credentials" / generation)

    def credentials(self) -> dict[str, str]:
        return credential_payload(
            self.secret_dir, sealed=self.config.get("credential_generation") is not None
        )

    def phase(self, phase: str, **metadata) -> None:
        self.config["operation"]["phase"] = phase
        self.config["operation"].update(metadata)
        self.save()

    def begin(self, kind: str, **metadata) -> None:
        if self.config["state"] not in HEALTHY:
            raise Failed("Interrupted operation; inspect status and use recover or fresh restore")
        self.config["state"] = kind + "-in-progress"
        self.config["operation"] = {
            "id": secrets.token_hex(16),
            "kind": kind,
            "phase": "planned",
            "previous_app_image": self.config["app_image"],
            "previous_credential_generation": self.config.get("credential_generation"),
            "previous_credential_volume": self.config["credential_volume"],
            **metadata,
        }
        self.save()

    def complete(self) -> None:
        self.config["operation"]["phase"] = "complete"
        self.config["state"] = "ready"
        self.save()

    def quiesce(self) -> None:
        self.phase("stopping-writers")
        self.compose("stop", "gateway", "api", "worker")
        for service in ("gateway", "api", "worker"):
            ids = self.compose("ps", "-a", "-q", service).decode().split()
            for identifier in ids:
                info = self.inspect("container", identifier)
                if (
                    info["Config"]["Labels"].get("com.docker.compose.project")
                    != self.config["project"]
                ):
                    raise Failed("Unexpected writer ownership")
                if info["State"]["Status"] not in ("exited", "created"):
                    raise Failed("Writers did not stop; backup and migration refused")
        self.phase("quiesced")

    def inspect(self, resource: str, identifier: str) -> dict:
        return json.loads(call([*self.docker, resource, "inspect", identifier]))[0]

    def compose(self, *args: str, timeout: int = 300) -> bytes:
        config = self.config
        env = {
            "KV_APP_IMAGE": config["app_image"],
            "KV_GATEWAY_IMAGE": config["gateway_image"],
            "KV_SECRETS_DIR": str(self.secret_dir),
            "KV_DATABASE_VOLUME": config["database_volume"],
            "KV_CREDENTIAL_VOLUME": config["credential_volume"],
            "KV_PORT": str(config["port"]),
        }
        if config.get("model_dir"):
            env["KV_MODEL_DIR"] = config["model_dir"]
            env["KV_MODEL_ID"] = config["model_manifest_sha256"]
            env["KV_MODEL_MANIFEST"] = config["model_manifest_path"]
        # Explicit env-file and -p prevent ambient .env/COMPOSE_FILE/project selection.
        env_file = self.path / "compose.env"
        text = "".join(f"{key}='{value}'\n" for key, value in env.items())
        if not env_file.exists() or read_private(env_file) != text.encode():
            atomic_private(env_file, text.encode())
        command = [
            *self.docker,
            "compose",
            "--env-file",
            str(env_file),
            "-p",
            config["project"],
            "-f",
            str(PACKAGE / "compose.yaml"),
            "-f",
            str(PACKAGE / "compose.docker.yaml"),
        ]
        if config.get("model_dir"):
            command += ["-f", str(PACKAGE / "compose.full.yaml")]
        # Compose interpolation prefers environment over env-file. Remove all overrides.
        with clean_compose_environment():
            return call([*command, *args], timeout=timeout)

    def container(self, service: str) -> str:
        identifier = self.compose("ps", "-a", "-q", service).decode().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise Failed(f"Expected exactly one {service} container")
        info = self.inspect("container", identifier)
        labels = info["Config"]["Labels"]
        if (
            labels.get("com.docker.compose.project") != self.config["project"]
            or labels.get("com.docker.compose.service") != service
        ):
            raise Failed("Unexpected container ownership")
        return identifier

    def wait(self, service: str) -> None:
        identifier = self.container(service)
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            state = self.inspect("container", identifier)["State"]
            if state["Status"] != "running":
                raise Failed(f"{service} stopped before readiness")
            health = state.get("Health", {}).get("Status")
            if health == "healthy":
                return
            if health == "unhealthy":
                raise Failed(f"{service} is unhealthy")
            time.sleep(2)
        raise Failed(f"{service} readiness timed out")

    def fresh_volume(self, image: str, destination: str) -> str:
        # Omit the source: the Engine allocates a NEW anonymous volume atomically.
        # Retain the stopped anchor for recovery; never guess a name or adopt a volume.
        intent = {
            "name": self.config["project"] + "-anchor-" + secrets.token_hex(12),
            "image": image,
            "destination": destination,
        }
        # A lost Docker reply leaves an identifiable, never-adopted resource, not an
        # unrecorded allocation. Retrying allocates another fresh volume.
        self.config.setdefault("resource_intents", []).append(intent)
        self.save()
        identifier = (
            call(
                [
                    *self.docker,
                    "create",
                    "--pull=never",
                    "--name",
                    intent["name"],
                    "--network",
                    "none",
                    "--label",
                    "knowledge-vault.instance=" + self.config["project"],
                    "--mount",
                    "type=volume,dst=" + destination,
                    "--entrypoint",
                    "/bin/true",
                    image,
                ]
            )
            .decode()
            .strip()
        )
        self.config.setdefault("anchors", []).append(identifier)
        intent["container_id"] = identifier
        self.save()
        mounts = self.inspect("container", identifier)["Mounts"]
        volume = next(
            m["Name"] for m in mounts if m["Destination"] == destination and m["Type"] == "volume"
        )
        return volume

    def start(self, *, migrate: bool = True) -> None:
        self.begin("start")
        try:
            self.quiesce()
            self.start_runtime(migrate=migrate)
            self.complete()
        except Exception:
            self.fail()
            raise

    def check_model(self) -> None:
        if self.config.get("model_dir"):
            expected = self.config.get("model_manifest_sha256")
            if not expected:
                raise Failed("Legacy full profile needs a manifest-bound fresh restore and rebuild")
            verify_host_model(
                Path(self.config["model_dir"]), Path(self.config["model_manifest_path"]), expected
            )

    def fail(self) -> None:
        with contextlib.suppress(Failed, OSError, subprocess.TimeoutExpired):
            self.compose("stop", "gateway", "api", "worker")
        # Do not overwrite the phase or recovery metadata with exception text.
        self.config["state"] = self.config["operation"]["kind"] + "-failed"
        self.save()

    def start_runtime(self, *, migrate: bool, recreate: bool = False) -> None:
        self.credentials()
        self.check_model()
        self.phase("starting-database")
        self.compose("up", "-d", "db")
        self.wait("db")
        if migrate:
            self.phase("migrating", migration_started=True)
            self.compose("--profile", "migration", "run", "--rm", "--no-deps", "migrate")
            # Commit forward-only recovery BEFORE any gateway exposure. A lost
            # migration reply remains conservative while the gateway is stopped.
            self.phase("migration-complete", migration_completed=True)
        baseline, _ = self.worker_clock()
        self.phase("starting-writers", worker_after=baseline)
        self.compose("up", "-d", *(["--force-recreate"] if recreate else []), "api", "worker")
        self.wait("api")
        self.wait_worker(baseline)
        if self.config["operation"]["kind"] == "upgrade" and not self.config["operation"].get(
            "migration_completed"
        ):
            raise Failed("Gateway exposure requires a durable successful migration")
        self.phase("starting-gateway")
        self.compose("up", "-d", "gateway")
        self.compose("restart", "gateway")  # nginx must resolve any new API container address.
        self.phase("verifying")
        self.verify(worker_after=baseline)

    def worker_clock(self) -> tuple[float, float]:
        # Operational metadata only. No assertion access and no database writes.
        value = call(
            [
                *self.docker,
                "exec",
                self.container("db"),
                "psql",
                "-U",
                "knowledge_vault",
                "-d",
                "knowledge_vault",
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--set=ON_ERROR_STOP=1",
                "--command",
                "SELECT extract(epoch FROM clock_timestamp()), COALESCE((SELECT "
                "extract(epoch FROM succeeded_at) FROM operational_heartbeats "
                "WHERE name='worker'),0)",
            ]
        )
        now, heartbeat = value.decode().strip().split("|")
        return float(now), float(heartbeat)

    def wait_worker(self, after: float, *, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while True:
            worker = self.inspect("container", self.container("worker"))
            if worker["State"]["Status"] != "running":
                raise Failed("Worker is not running")
            now, heartbeat = self.worker_clock()
            if heartbeat > after and 0 <= now - heartbeat <= 30:
                return
            if time.monotonic() >= deadline:
                raise Failed("Worker has no fresh successful maintenance heartbeat")
            time.sleep(1)

    def verify(self, *, worker_after: float | None = None) -> None:
        self.credentials()
        if worker_after is None:
            worker_after, _ = self.worker_clock()
        self.wait_worker(worker_after)
        ca = ssl.create_default_context(cafile=str(self.secret_dir / "tls-cert.pem"))
        token = read_private(self.secret_dir / "device-token").decode()
        request = urllib.request.Request(
            f"https://localhost:{self.config['port']}/api/v1/statistics",
            headers={"Authorization": "Bearer " + token},
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ca)
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                with opener.open(request, timeout=5) as response:
                    if response.status != 200:
                        raise Failed("Authenticated TLS verification failed")
                    statistics = json.load(response)
                    age = statistics["operations"]["worker_heartbeat_age_seconds"]
                    if age is None or not 0 <= age <= 30:
                        raise Failed("Worker operational heartbeat is stale")
                break
            except (urllib.error.URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise Failed("Authenticated TLS readiness timed out") from None
                time.sleep(2)
        for service in ("api", "worker", "db", "gateway"):
            identifier = self.container(service)
            info = self.inspect("container", identifier)
            if info["State"]["Status"] != "running" or info["RestartCount"]:
                raise Failed(f"{service} stopped or restarted unexpectedly")
            if service == "api":
                # Read logs after the client call; do not echo potentially private payloads.
                call([*self.docker, "logs", "--tail", "100", identifier])
        print("Authenticated TLS and fresh worker maintenance passed; zero automatic restarts.")

    def upgrade(
        self, image: str, repository: Path, password_file: Path, *, resume: bool = False
    ) -> None:
        if not PIN.fullmatch(image):
            raise Failed("Upgrade requires an immutable image reference")
        call([*self.docker, "image", "inspect", image])
        if not resume:
            self.begin(
                "upgrade",
                candidate_app_image=image,
                backup_repository=str(repository.absolute()),
                upgrade_recovery="forward-v1",
            )
        else:
            self.config["state"] = "upgrade-in-progress"
            self.config["operation"]["upgrade_recovery"] = "forward-v1"
            self.save()
        try:
            self.quiesce()
            self.phase("creating-backup")
            snapshot = self.repository("backup", repository, password_file)
            self.config["pre_upgrade_snapshot"] = snapshot
            self.config["previous_app_image"] = self.config["operation"]["previous_app_image"]
            self.phase("backup-verified", recovery_snapshot=snapshot)
            self.config["app_image"] = image
            self.phase("candidate-selected")
            self.start_runtime(migrate=True)
            self.complete()
        except Exception:
            self.fail()
            raise
        print("Upgrade verified; quiesced pre-upgrade snapshot: " + snapshot)

    def rotate(self, *, retire_previous: bool = False, resume: bool = False) -> None:
        if not resume:
            self.begin("rotation", retire_previous=retire_previous)
        else:
            self.config["state"] = "rotation-in-progress"
            self.save()
        try:
            operation = self.config["operation"]
            selected = operation.get("selected_generation")
            self.quiesce()
            if selected is None:
                generation = operation.get("candidate_generation")
                directory = self.path / "credentials" / (generation or "missing")
                if not (directory / "complete.json").is_file():
                    payload = self.credentials()
                    token = (
                        payload["device-token"]
                        if operation["retire_previous"]
                        else "kv_" + secrets.token_urlsafe(32)
                    )
                    records = json.loads(payload["bootstrap-tokens"])
                    if operation["retire_previous"]:
                        records = [r for r in records if r["principal_id"] != "standalone-device"]
                    records.append(
                        {
                            "principal_id": "standalone-device",
                            "scopes": ["knowledge:read", "knowledge:write"],
                            "sha256": hmac.new(
                                payload["token-pepper"].encode(), token.encode(), hashlib.sha256
                            ).hexdigest(),
                        }
                    )
                    payload.update({"device-token": token, "bootstrap-tokens": json.dumps(records)})
                    generation = secrets.token_hex(16)
                    self.phase("creating-generation", candidate_generation=generation)
                    create_generation(self.path, generation, payload)
                payload = credential_payload(self.path / "credentials" / generation)
                self.phase("seeding-generation")
                volume = seed_credentials(self, payload)
                self.phase("generation-prepared", candidate_volume=volume)
                # This single fsynced config replacement selects BOTH host files and
                # volume. No credentials are ever replaced one file at a time.
                self.config["credential_generation"] = generation
                self.config["credential_volume"] = volume
                self.phase("credentials-selected", selected_generation=generation)
            else:
                if self.config.get("credential_generation") != selected:
                    raise Failed(
                        "Credential journal disagrees with active generation; refusing rollback"
                    )
                self.credentials()
            self.start_runtime(migrate=False)
            self.complete()
        except Exception:
            self.fail()
            raise
        print(
            "Credential generation verified. "
            "Use credential-path to locate the current device token."
        )

    def recover(self, repository: Path | None = None, password_file: Path | None = None) -> None:
        operation = self.config.get("operation", {})
        kind = operation.get("kind")
        if self.config["state"] in HEALTHY:
            raise Failed("No interrupted operation to recover")
        if kind == "rotation":
            self.rotate(resume=True)
        elif kind == "upgrade":
            if operation.get("migration_completed"):
                # The candidate may already have acknowledged client writes.
                # Keep its database/image and never restore the pre-upgrade dump.
                try:
                    self.config["state"] = "upgrade-in-progress"
                    self.save()
                    self.quiesce()
                    self.start_runtime(migrate=False)
                    self.complete()
                except Exception:
                    self.fail()
                    raise
                return
            if operation.get("migration_started"):
                self.quiesce()
                if operation.get("upgrade_recovery") != "forward-v1":
                    raise Failed(
                        "Legacy interrupted upgrade may have accepted writes; preserve the "
                        "candidate database for operator-led forward repair, not snapshot rollback"
                    )
                raise Failed(
                    "Migration may have begun; restore pre_upgrade_snapshot "
                    "with previous_app_image into a NEW instance"
                )
            if repository is None or password_file is None:
                raise Failed("Pre-migration upgrade recovery requires repository and password-file")
            self.upgrade(operation["candidate_app_image"], repository, password_file, resume=True)
        elif kind in ("start", "recreate"):
            self.quiesce()
            self.start_runtime(migrate=False)
            self.complete()
        else:
            raise Failed(
                "Incomplete initialization/restore: retain evidence and use a NEW state directory"
            )

    def repository(
        self, action: str, repository: Path, password_file: Path, snapshot: str = ""
    ) -> str:
        repository = private_directory(repository)
        password = read_private(password_file)
        if not password or len(password) < 16:
            raise Failed("Restic password file must contain at least 16 bytes")
        db_password = read_private(self.secret_dir / "postgres-password")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as output:
            for name, value in {
                "restic-password": password,
                "recovery-pepper": read_private(self.secret_dir / "token-pepper"),
                "pgpass": b"db:5432:knowledge_vault:knowledge_vault:" + db_password + b"\n",
            }.items():
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(value), 0o600
                output.addfile(member, io.BytesIO(value))
        network = (
            self.config["project"] + "_private"
            if action not in ("init", "recovery-pepper")
            else "none"
        )
        args = [
            *self.docker,
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--network",
            network,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "2g",
            "--tmpfs",
            f"/scratch:uid={os.getuid()},gid={os.getgid()},mode=0700,size=1073741824",
            "--tmpfs",
            "/tmp:mode=1777,size=67108864",  # noqa: S108  # Private container tmpfs.
            "--mount",
            f"type=bind,src={repository},dst=/repository"
            + (",readonly" if action in ("restore", "recovery-pepper") else ""),
            "--mount",
            f"type=bind,src={PACKAGE / 'repository.sh'},dst=/run/repository.sh,readonly",
            "--entrypoint",
            "/bin/bash",
            self.config["backup_image"],
            "/run/repository.sh",
            action,
            snapshot,
        ]
        result = call(args, data=archive.getvalue(), timeout=1800).decode()
        if action == "backup":
            summaries = [json.loads(line) for line in result.splitlines() if line.startswith("{")]
            return next(
                row["snapshot_id"] for row in summaries if row.get("message_type") == "summary"
            )
        if action == "recovery-pepper":
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", result):
                raise Failed("Recovery key is missing or invalid; writers remain stopped")
            return result
        return ""


@contextlib.contextmanager
def clean_compose_environment():
    removed = {
        key: os.environ.pop(key) for key in list(os.environ) if key.startswith(("KV_", "COMPOSE_"))
    }
    try:
        yield
    finally:
        os.environ.update(removed)


def credential_payload(directory: Path, *, sealed: bool = True) -> dict[str, str]:
    directory = private_directory(directory)
    payload = {name: read_private(directory / name).decode() for name in CREDENTIAL_FILES}
    if sealed:
        seal = json.loads(read_private(directory / "complete.json"))
        if seal.get("files") != {
            name: hashlib.sha256(value.encode()).hexdigest() for name, value in payload.items()
        }:
            raise Failed("Credential generation is incomplete or has changed")
    expected = hmac.new(
        payload["token-pepper"].encode(), payload["device-token"].encode(), hashlib.sha256
    ).hexdigest()
    if not any(
        hmac.compare_digest(record["sha256"], expected)
        for record in json.loads(payload["bootstrap-tokens"])
    ):
        raise Failed("Device token and bootstrap records disagree")
    return payload


def verify_host_model(model: Path, manifest: Path, expected: str | None = None) -> dict:
    # Reuse the application's pure-stdlib implementation of the existing M1 manifest
    # file format without requiring the operator to install server dependencies.
    verify = runpy.run_path(str(PACKAGE.parents[1] / "src/knowledge_vault/embeddings/manifest.py"))[
        "verify_model_manifest"
    ]
    try:
        return verify(model, manifest, expected_digest=expected, dimensions=384)
    except (ValueError, OSError):
        raise Failed(
            "Model manifest verification failed; files must match the provisioned snapshot"
        ) from None


def seal_generation(directory: Path) -> None:
    payload = credential_payload(directory, sealed=False)
    for name in CREDENTIAL_FILES:
        with (directory / name).open("rb") as source:
            os.fsync(source.fileno())
    private_file(
        directory / "complete.json",
        json.dumps(
            {
                "schema_version": 1,
                "files": {
                    name: hashlib.sha256(value.encode()).hexdigest()
                    for name, value in payload.items()
                },
            }
        ).encode(),
    )
    sync_directory(directory)
    sync_directory(directory.parent)


def create_generation(path: Path, generation: str, payload: dict[str, str]) -> None:
    root = path / "credentials"
    if not root.exists():
        root.mkdir(mode=0o700)
        sync_directory(path)
    private_directory(root)
    directory = root / generation
    directory.mkdir(mode=0o700)
    sync_directory(root)
    for name in CREDENTIAL_FILES:
        private_file(directory / name, payload[name].encode())
    seal_generation(directory)


def initialize(args: argparse.Namespace, *, restoring: bool = False) -> Instance:
    path = args.state.absolute()
    if path.resolve() != path:
        raise Failed("State path must not contain symlink components")
    if not 1024 <= args.port <= 65535:
        raise Failed("Choose an unprivileged TCP port")
    model_info = None
    manifest = None
    if args.model_dir:
        model = args.model_dir.absolute()
        if model.resolve() != model or not model.is_dir():
            raise Failed("Model directory must already exist without symlink components")
        if any(character in str(model) for character in ",\n\r\0'"):
            raise Failed("Unsupported model directory path")
        manifest = (args.model_manifest or model.with_name(model.name + ".json")).absolute()
        if manifest.resolve() != manifest or any(c in str(manifest) for c in ",\n\r\0'"):
            raise Failed("Unsafe model manifest path")
        model_info = verify_host_model(model, manifest, args.model_manifest_sha256)
    for image in (args.app_image, args.backup_image, args.gateway_image):
        if not PIN.fullmatch(image):
            raise Failed("Images require immutable registry digests or complete local sha256 IDs")
    context = call(["docker", "context", "show"]).decode().strip()
    compose_version = (
        call(["docker", "--context", context, "compose", "version", "--short"]).decode().strip()
    )
    endpoint = json.loads(call(["docker", "context", "inspect", context]))[0]["Endpoints"][
        "docker"
    ]["Host"]
    if not endpoint.startswith("unix://") or os.environ.get("DOCKER_HOST"):
        raise Failed(
            "This controller requires a local Linux Docker Unix socket; "
            "remote engines are unverified"
        )
    info = json.loads(call(["docker", "--context", context, "info", "--format", "{{json .}} "]))
    if info["OSType"] != "linux":
        raise Failed("Only Linux Docker is verified by this controller")
    for image in (args.app_image, args.backup_image, args.gateway_image, DB_IMAGE):
        call(["docker", "--context", context, "image", "inspect", image])
    # The caller exclusively created the directory and holds its lifecycle lock.
    private_directory(path)
    generation = secrets.token_hex(16)
    config = {
        "project": "kv" + secrets.token_hex(12),
        "context": context,
        "engine_id": info["ID"],
        "engine_version": info["ServerVersion"],
        "compose_version": compose_version,
        "app_image": args.app_image,
        "backup_image": args.backup_image,
        "gateway_image": args.gateway_image,
        "port": args.port,
        "model_dir": str(args.model_dir.resolve()) if args.model_dir else None,
        "model_manifest_path": str(manifest) if manifest else None,
        "model_manifest_sha256": model_info["manifest_sha256"] if model_info else None,
        "state": "initializing",
        "credential_generation": generation,
        "operation": {
            "id": secrets.token_hex(16),
            "kind": "restore" if restoring else "init",
            "phase": "planned",
        },
    }
    instance = Instance(path, config)
    instance.save()
    provision = runpy.run_path(str(PACKAGE / "provision-secrets.py"))["provision"]
    (path / "credentials").mkdir(mode=0o700)
    sync_directory(path)
    secret_dir = path / "credentials" / generation
    provision(secret_dir)
    call(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-days",
            "365",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(secret_dir / "tls-key.pem"),
            "-out",
            str(secret_dir / "tls-cert.pem"),
        ]
    )
    (secret_dir / "tls-key.pem").chmod(0o600)
    (secret_dir / "tls-cert.pem").chmod(0o600)
    seal_generation(secret_dir)
    config["database_volume"] = instance.fresh_volume(DB_IMAGE, "/var/lib/postgresql/data")
    instance.save()
    payload = instance.credentials()
    config["credential_volume"] = seed_credentials(instance, payload)
    config["state"] = "restore-in-progress" if restoring else "initializing"
    config["operation"]["phase"] = "provisioned"
    instance.save()
    return instance


def seed_credentials(instance: Instance, payload: dict[str, str]) -> str:
    volume = instance.fresh_volume(instance.config["app_image"], "/run/kv-secrets")
    call(
        [
            *instance.docker,
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--network",
            "none",
            "--user",
            "0:0",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--pids-limit",
            "32",
            "--memory",
            "128m",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            "type=volume,src=" + volume + ",dst=/run/kv-secrets",
            "--mount",
            f"type=bind,src={PACKAGE / 'volume-init.py'},dst=/run/init.py,readonly",
            "--entrypoint",
            "python",
            instance.config["app_image"],
            "/run/init.py",
        ],
        data=json.dumps(
            {name: value for name, value in payload.items() if name != "device-token"}
        ).encode(),
    )
    return volume


def restore_instance(instance: Instance, args: argparse.Namespace) -> None:
    try:
        instance.phase(
            "starting-restore",
            recovery_snapshot=args.snapshot,
            backup_repository=str(args.repository.absolute()),
        )
        instance.compose("up", "-d", "db")
        instance.wait("db")
        db = instance.container("db")
        network = instance.inspect("network", instance.config["project"] + "_private")
        mounts = instance.inspect("container", db)["Mounts"]
        if set(network["Containers"]) != {db} or not any(
            m.get("Name") == instance.config["database_volume"]
            and m["Destination"] == "/var/lib/postgresql/data"
            for m in mounts
        ):
            raise Failed("Restore target isolation or volume identity differs")
        instance.phase("restoring-database")
        instance.repository("restore", args.repository, args.password_file, args.snapshot)
        pepper = instance.repository(
            "recovery-pepper", args.repository, args.password_file, args.snapshot
        )
        payload = instance.credentials()
        payload["token-pepper"] = pepper
        records = json.loads(payload["bootstrap-tokens"])
        for record in records:
            record["sha256"] = hmac.new(
                pepper.encode(), payload["device-token"].encode(), hashlib.sha256
            ).hexdigest()
        payload["bootstrap-tokens"] = json.dumps(records)
        generation = secrets.token_hex(16)
        instance.phase("creating-recovery-generation", candidate_generation=generation)
        create_generation(instance.path, generation, payload)
        volume = seed_credentials(instance, payload)
        instance.config["credential_generation"] = generation
        instance.config["credential_volume"] = volume
        instance.config["restored_snapshot"] = args.snapshot
        instance.phase("credentials-selected", selected_generation=generation)
        instance.start_runtime(migrate=False)
        instance.complete()
        print("Fresh restore verified. New device/DB/TLS credentials; receipt HMAC key preserved.")
    except Exception:
        instance.fail()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--state",
        type=Path,
        required=True,
        help="private instance directory; must be new for init/restore",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("--app-image", required=True)
        sub.add_argument("--backup-image", required=True)
        sub.add_argument("--gateway-image", default=GATEWAY_IMAGE)
        sub.add_argument("--port", type=int, default=8443)
        sub.add_argument("--model-dir", type=Path)
        sub.add_argument("--model-manifest", type=Path)
        sub.add_argument("--model-manifest-sha256")
    for name in ("start", "stop", "status", "verify", "recreate", "reembed", "credential-path"):
        commands.add_parser(name)
    upgrade = commands.add_parser("upgrade")
    upgrade.add_argument("--app-image", required=True)
    rotate = commands.add_parser("rotate-token")
    rotate.add_argument("--retire-previous", action="store_true")
    backup = commands.add_parser("backup")
    backup.add_argument("--init-repository", action="store_true")
    for sub in (backup, upgrade, commands.choices["restore"]):
        sub.add_argument("--repository", type=Path, required=True)
        sub.add_argument("--password-file", type=Path, required=True)
    recovery = commands.add_parser("recover")
    recovery.add_argument("--repository", type=Path)
    recovery.add_argument("--password-file", type=Path)
    commands.choices["restore"].add_argument("--snapshot", required=True)
    args = parser.parse_args()
    try:
        if os.getuid() == 0:
            raise Failed("Run the controller as a non-root operator with Docker access")
        creating = args.command in ("init", "restore")
        if args.command == "restore" and not re.fullmatch(r"[0-9a-f]{64}", args.snapshot):
            raise Failed("Restore requires an explicit complete snapshot ID")
        with lifecycle_lock(args.state, create=creating) as path:
            # Mutable config is first opened AFTER the exclusive lock.
            instance = (
                initialize(args, restoring=args.command == "restore")
                if creating
                else Instance(path, json.loads(read_private(path / "config.json")))
            )
            if (
                args.command not in ("init", "restore", "status", "stop", "recover")
                and instance.config["state"] not in HEALTHY
            ):
                raise Failed(
                    "Interrupted operation; status/recover required before ordinary commands"
                )
            if args.command == "init":
                instance.complete()
                print(
                    "Initialized private instance. Start it, "
                    "then use credential-path for client setup."
                )
            elif args.command == "restore":
                restore_instance(instance, args)
            elif args.command == "start":
                instance.start()
            elif args.command == "upgrade":
                instance.upgrade(args.app_image, args.repository, args.password_file)
            elif args.command == "rotate-token":
                instance.rotate(retire_previous=args.retire_previous)
            elif args.command == "recover":
                instance.recover(args.repository, args.password_file)
            elif args.command == "credential-path":
                instance.credentials()
                print(instance.secret_dir)
            elif args.command == "reembed":
                if not instance.config.get("model_dir"):
                    raise Failed("Re-embedding requires the full offline model profile")
                instance.check_model()
                instance.compose(
                    "run", "--rm", "--no-deps", "--entrypoint", "knowledge-vault-reembed", "api"
                )
                print("Rebuild queued for the configured manifest-bound model.")
            elif args.command == "recreate":
                instance.begin("recreate")
                try:
                    instance.quiesce()
                    instance.phase("recreating-database")
                    instance.compose("up", "-d", "--force-recreate", "db")
                    instance.start_runtime(migrate=True, recreate=True)
                    instance.complete()
                except Exception:
                    instance.fail()
                    raise
            elif args.command == "stop":
                instance.compose("stop")
            elif args.command == "status":
                print(
                    json.dumps(
                        {
                            key: instance.config.get(key)
                            for key in (
                                "state",
                                "configuration_generation",
                                "credential_generation",
                                "credential_volume",
                                "database_volume",
                                "resource_intents",
                                "anchors",
                                "previous_app_image",
                                "pre_upgrade_snapshot",
                                "operation",
                            )
                        },
                        indent=2,
                    )
                )
            elif args.command == "verify":
                instance.verify()
            elif args.command == "backup":
                if args.init_repository:
                    if any(private_directory(args.repository).iterdir()):
                        raise Failed(
                            "Repository initialization requires an empty private directory"
                        )
                    instance.repository("init", args.repository, args.password_file)
                print(
                    "Verified backup snapshot: "
                    + instance.repository("backup", args.repository, args.password_file)
                )
    except Failed as exc:
        print(str(exc) + ". Resources and durable operation state retained.", file=sys.stderr)
        return 1
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, StopIteration):
        print(
            "Operation failed. Inspect status/phase and recover; "
            "no secret diagnostics are emitted.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
