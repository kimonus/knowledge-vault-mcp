from enum import StrEnum


class AssertionKind(StrEnum):
    USER_FACT = "user_fact"
    PREFERENCE = "preference"
    EXTERNAL_FACT = "external_fact"
    DERIVED_CONCLUSION = "derived_conclusion"
    DECISION = "decision"
    PROCEDURE = "procedure"
    CONFIGURATION = "configuration"
    CONSIDERED_OPTION = "considered_option"
    REJECTED_OPTION = "rejected_option"
    PLAN = "plan"
    OPEN_QUESTION = "open_question"
    ARTIFACT_OBSERVATION = "artifact_observation"


class AssertionOrigin(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    JOINT = "joint"
    EXTERNAL_SOURCE = "external_source"
    ARTIFACT = "artifact"


class AssertionStatus(StrEnum):
    CURRENT = "current"
    UNCERTAIN = "uncertain"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class EmbeddingState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    DISABLED = "disabled"


class BatchState(StrEnum):
    OPEN = "open"
    COMMITTED = "committed"
    ABORTED = "aborted"
    EXPIRED = "expired"


class JobState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RETRY = "retry"
    COMPLETED = "completed"
    DEAD = "dead"


class ArtifactState(StrEnum):
    OPEN = "open"
    STORED = "stored"
    # The same content was already stored; `duplicate_of` names the record that holds it.
    DUPLICATE = "duplicate"


class ArtifactStorage(StrEnum):
    """How an artifact's content is held. Binary storage would be a further member."""

    TEXT = "text"
