"""Synthetic source packs and independently specified expected claims.

The sources and expected claims are authored here before the service runs. The service's
responses never define relevance or truth. Template siblings are separate scenarios but
share a pattern; the report labels them so they cannot masquerade as independent human cases.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GoldClaim:
    key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class StateGold:
    request: dict[str, Any]
    outcome: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    split: str
    source: str
    claims: tuple[GoldClaim, ...]
    query: str | None = None
    relevant: tuple[str, ...] = ()
    state: StateGold | None = None
    requires_abstention: bool = False


def _claim(key: str, content: str, kind: str, **extra: Any) -> GoldClaim:
    return GoldClaim(key, {"content": content, "kind": kind, "origin": "user", **extra})


def _identity(subject: str, property_name: str, scope: str = "home") -> dict[str, str]:
    return {
        "subject": subject,
        "property": property_name,
        "scope": scope,
        "state_type": "configuration",
    }


def _temporal(identity: dict[str, str], key: str, value: str, mode: str) -> dict[str, Any]:
    return {"identity": identity, "evidence_key": key, "value": value, "mode": mode}


def cases() -> tuple[Case, ...]:
    result: list[Case] = []
    splits = ("dev", "dev", "dev", "dev", "held_out", "held_out")

    # Each source states the chronology explicitly; gold dates and values are not inferred
    # from the order in which the backend receives the records.
    transitions = (
        ("gateway", "resolver", "unbound", "knot"),
        ("nas", "backup.target", "disk-one", "disk-two"),
        ("router", "dns.upstream", "provider-a", "provider-b"),
        ("workstation", "audio.output", "speaker-a", "speaker-b"),
        ("camera", "storage.mode", "local-only", "encrypted-remote"),
        ("printer", "network.mode", "wireless", "wired"),
    )
    for index, (subject, prop, old, new) in enumerate(transitions):
        identity = _identity(f"current-{subject}", prop)
        prefix = f"current-{index}"
        old_key, new_key = f"{prefix}-old", f"{prefix}-new"
        source = (
            f"From 2026-01-01 through 2026-01-31 the home {subject} {prop} was {old}. "
            f"From 2026-02-01 it was {new}; the change was implemented."
        )
        claims = (
            _claim(
                old_key,
                f"The home {subject} {prop} was {old} on 2026-01-01.",
                "configuration",
                evidence_state="reported",
                observed_at="2026-01-01T00:00:00Z",
                valid_from="2026-01-01T00:00:00Z",
                valid_to="2026-02-01T00:00:00Z",
                temporal=_temporal(identity, old_key, old, "snapshot"),
            ),
            _claim(
                new_key,
                f"The home {subject} {prop} changed to {new} on 2026-02-01.",
                "configuration",
                evidence_state="reported",
                observed_at="2026-02-01T00:00:00Z",
                valid_from="2026-02-01T00:00:00Z",
                temporal=_temporal(identity, new_key, new, "change"),
            ),
        )
        result.append(
            Case(
                prefix,
                "current_configuration",
                splits[index],
                source,
                claims,
                query=f"What is the current home {subject} {prop}?",
                relevant=(new_key,),
                state=StateGold({"identity": identity}, "resolved", (new,)),
            )
        )
        result.append(
            Case(
                f"history-{index}",
                "historical_configuration",
                splits[index],
                source,
                tuple(
                    GoldClaim(
                        f"history-{index}-{suffix}",
                        {
                            **claim.payload,
                            "content": claim.payload["content"].replace(
                                "The home", "Historically, the home"
                            ),
                            "temporal": {
                                **claim.payload["temporal"],
                                "identity": _identity(f"history-{subject}", prop),
                                "evidence_key": f"history-{index}-{suffix}",
                            },
                        },
                    )
                    for suffix, claim in (("old", claims[0]), ("new", claims[1]))
                ),
                state=StateGold(
                    {
                        "identity": _identity(f"history-{subject}", prop),
                        "as_of": "2026-01-15T00:00:00Z",
                    },
                    "resolved",
                    (old,),
                ),
            )
        )

    choices = (
        ("battery", "cell-a", "cell-b", "lower idle noise"),
        ("router", "model-r", "model-s", "longer security support"),
        ("monitor", "panel-c", "panel-d", "better text clarity"),
        ("keyboard", "board-e", "board-f", "replaceable switches"),
        ("storage", "drive-g", "drive-h", "lower power draw"),
        ("headset", "set-i", "set-j", "more comfortable fit"),
    )
    for index, (item, chosen, rejected, reason) in enumerate(choices):
        prefix = f"decision-{index}"
        selected = f"{prefix}-choice"
        declined = f"{prefix}-declined"
        result.append(
            Case(
                prefix,
                "decision_rationale",
                splits[index],
                f"For the {item}, I selected {chosen} because of {reason}. I rejected {rejected}.",
                (
                    _claim(
                        selected,
                        f"The user selected {chosen} for the {item} because of {reason}.",
                        "decision",
                    ),
                    _claim(
                        declined, f"The user rejected {rejected} for the {item}.", "rejected_option"
                    ),
                ),
                query=f"Why did the user select {chosen} for the {item}?",
                relevant=(selected,),
            )
        )

    preferences = (
        ("desk lamp", "warm white light"),
        ("travel bag", "lightweight material"),
        ("coffee grinder", "low noise"),
        ("text editor", "keyboard navigation"),
        ("home dashboard", "high contrast"),
        ("phone", "long software support"),
    )
    for index, (item, feature) in enumerate(preferences):
        key = f"preference-{index}-claim"
        result.append(
            Case(
                f"preference-{index}",
                "product_preference",
                splits[index],
                f"When choosing a {item}, I prefer {feature}.",
                (_claim(key, f"The user prefers {feature} in a {item}.", "preference"),),
                query=f"What matters to the user when choosing a {item}?",
                relevant=(key,),
            )
        )

    purchases = (
        ("tablet", "slate-a", "slate-b"),
        ("camera", "camera-c", "camera-d"),
        ("laptop", "notebook-e", "notebook-f"),
        ("vacuum", "vacuum-g", "vacuum-h"),
        ("microphone", "mic-i", "mic-j"),
        ("projector", "projector-k", "projector-l"),
    )
    for index, (item, considered, purchased) in enumerate(purchases):
        prefix = f"purchase-{index}"
        bought_key = f"{prefix}-bought"
        result.append(
            Case(
                prefix,
                "considered_vs_purchased",
                splits[index],
                f"I considered {considered} for a {item}, but purchased {purchased} instead.",
                (
                    _claim(
                        f"{prefix}-considered",
                        f"The user considered {considered} for a {item}.",
                        "considered_option",
                    ),
                    _claim(bought_key, f"The user purchased {purchased} as a {item}.", "user_fact"),
                ),
                query=f"Which {item} did the user purchase?",
                relevant=(bought_key,),
            )
        )

    repairs = (
        ("network timeout", "increase retries", "repair DNS"),
        ("backup failure", "restart scheduler", "fix repository mount"),
        ("audio crackle", "replace cable", "change sample rate"),
        ("printer jam", "clean rollers", "replace worn feed gear"),
        ("slow build", "clear cache", "remove duplicate compiler flags"),
        ("camera disconnect", "reboot camera", "replace faulty power supply"),
    )
    for index, (issue, failed, worked) in enumerate(repairs):
        prefix = f"repair-{index}"
        failed_key, worked_key = f"{prefix}-failed", f"{prefix}-worked"
        result.append(
            Case(
                prefix,
                "troubleshooting",
                splits[index],
                f"For {issue}, I tried {failed} and it failed. Then I used {worked} and it worked.",
                (
                    _claim(
                        failed_key,
                        f"For {issue}, {failed} did not solve the problem.",
                        "artifact_observation",
                    ),
                    _claim(worked_key, f"For {issue}, {worked} solved the problem.", "procedure"),
                ),
                query=f"What was tried for {issue}, and what worked?",
                relevant=(failed_key, worked_key),
            )
        )

    corrections = (
        ("sensor", "firmware.version", "v1", "v2"),
        ("switch", "port.count", "8", "16"),
        ("display", "refresh.rate", "60hz", "75hz"),
        ("ups", "battery.capacity", "800va", "1000va"),
        ("server", "memory.capacity", "16gb", "32gb"),
        ("router", "model.identifier", "rx10", "rx20"),
    )
    for index, (subject, prop, wrong, right) in enumerate(corrections):
        prefix = f"correction-{index}"
        old_key, new_key = f"{prefix}-wrong", f"{prefix}-right"
        identity = _identity(f"correction-{subject}", prop)
        result.append(
            Case(
                prefix,
                "user_correction",
                splits[index],
                (
                    f"I first reported {subject} {prop} as {wrong}; that was a mistake. "
                    f"The correct value is {right}."
                ),
                (
                    _claim(
                        old_key,
                        f"The {subject} {prop} was reported as {wrong}.",
                        "configuration",
                        evidence_state="reported",
                        observed_at="2026-01-01T00:00:00Z",
                        temporal=_temporal(identity, old_key, wrong, "snapshot"),
                    ),
                    _claim(
                        new_key,
                        f"The {subject} {prop} was corrected to {right}.",
                        "configuration",
                        evidence_state="reported",
                        observed_at="2026-02-01T00:00:00Z",
                        temporal={
                            **_temporal(identity, new_key, right, "correction"),
                            "corrects_key": old_key,
                        },
                    ),
                ),
                state=StateGold({"identity": identity}, "resolved", (right,)),
            )
        )

    integrations = (
        ("garden-planner", "offline mode", "CSV export"),
        ("travel-journal", "map cache", "photo captions"),
        ("media-server", "LAN playback", "subtitle search"),
        ("budget-tool", "local storage", "monthly export"),
        ("recipe-book", "metric units", "ingredient scaling"),
        ("fitness-log", "private notes", "weekly summary"),
    )
    for index, (project, first, second) in enumerate(integrations):
        prefix = f"integration-{index}"
        first_key, second_key = f"{prefix}-first", f"{prefix}-second"
        result.append(
            Case(
                prefix,
                "multi_source_integration",
                splits[index],
                (
                    f"Conversation A: {project} requires {first}. "
                    f"Conversation B: The same project also requires {second}."
                ),
                (
                    _claim(first_key, f"The {project} project requires {first}.", "plan"),
                    _claim(second_key, f"The {project} project requires {second}.", "plan"),
                ),
                query=f"What does the {project} project require?",
                relevant=(first_key, second_key),
            )
        )

    multilingual = (
        ("drukarka", "obsługa dupleksu", "Jaką funkcję drukarki preferuje użytkownik?"),
        ("podróż", "pociągi nocne", "Jaki środek podróży użytkownik preferuje?"),
        ("klawiatura", "ciche przełączniki", "Jaką klawiaturę woli użytkownik?"),
        ("камера", "локальное хранение", "Что важно пользователю для камеры?"),
        ("роутер", "долгая поддержка", "Что важно пользователю для роутера?"),
        ("наушники", "удобная посадка", "Какие наушники предпочитает пользователь?"),
    )
    for index, (item, feature, query) in enumerate(multilingual):
        key = f"multilingual-{index}-claim"
        result.append(
            Case(
                f"multilingual-{index}",
                "multilingual",
                splits[index],
                f"Użytkownik / пользователь: {item} — {feature}.",
                (_claim(key, f"For {item}, the user prefers {feature}.", "preference"),),
                query=query,
                relevant=(key,),
            )
        )

    for index in range(3):
        item = ("solar-battery", "bike-lock", "air-purifier")[index]
        mention_key = f"missing-{index}-mention"
        result.append(
            Case(
                f"missing-{index}",
                "missing_or_conflicting",
                splits[index],
                f"The source mentions {item} but gives no purchase date or model.",
                (
                    _claim(
                        mention_key,
                        f"The source does not identify a purchased model for {item}.",
                        "artifact_observation",
                        origin="artifact",
                    ),
                ),
                query=f"What exact model of {item} did the user purchase?",
                relevant=(mention_key,),
                requires_abstention=True,
            )
        )
    for index in range(3, 6):
        subject = ("thermostat", "network-switch", "storage-box")[index - 3]
        identity = _identity(f"conflict-{subject}", "installed.location")
        prefix = f"conflict-{index}"
        left, right = f"{prefix}-a", f"{prefix}-b"
        result.append(
            Case(
                prefix,
                "missing_or_conflicting",
                splits[index],
                f"Two equally dated notes disagree: {subject} is in room-a; another says room-b.",
                (
                    _claim(
                        left,
                        f"The {subject} is installed in room-a.",
                        "configuration",
                        evidence_state="reported",
                        observed_at="2026-01-01T00:00:00Z",
                        temporal=_temporal(identity, left, "room-a", "snapshot"),
                    ),
                    _claim(
                        right,
                        f"The {subject} is installed in room-b.",
                        "configuration",
                        evidence_state="reported",
                        observed_at="2026-01-01T00:00:00Z",
                        temporal=_temporal(identity, right, "room-b", "snapshot"),
                    ),
                ),
                state=StateGold({"identity": identity}, "conflict", ("room-a", "room-b")),
            )
        )

    for index, subject in enumerate(("violin", "kayak", "espresso-machine")):
        result.append(
            Case(
                f"no-record-{index}",
                "no_record",
                "dev" if index == 0 else "held_out",
                "No source material supplied for this question.",
                (),
                query=f"Which {subject} model did the user purchase?",
                requires_abstention=True,
            )
        )

    assert len(result) == 63
    assert len({case.id for case in result}) == 63
    return tuple(result)
