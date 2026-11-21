"""Build a policy-only filter transaction; never flush service/NAT/IPVS state."""

import shlex


def owned_chain(name: str) -> bool:
    return name in {
        "KUBE-ROUTER-INPUT",
        "KUBE-ROUTER-FORWARD",
        "KUBE-ROUTER-OUTPUT",
    } or name.startswith(("KUBE-POD-FW-", "KUBE-NWPLCY-"))


def cleanup_transaction(snapshot: str) -> str | None:
    """Detach only jumps into reserved netpol chains, then delete those chains atomically.

    Use with iptables-restore --test --noflush, then --wait 10 --noflush, after
    stopping the controller. Installation must refuse preexisting reserved chains.
    This deliberately does not call kube-router --cleanup-config, which flushes IPVS.
    """
    lines = snapshot.splitlines()
    if sum(line == "*filter" for line in lines) != 1 or lines.count("COMMIT") != 1:
        raise ValueError("Expected exactly one filter table")
    chains: set[str] = set()
    rules: list[tuple[str, list[str]]] = []
    active = False
    complete = False
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if line == "*filter" and not active and not complete:
            active = True
            continue
        if line == "COMMIT" and active:
            complete, active = True, False
            continue
        if not active:
            raise ValueError("Unexpected content outside filter table")
        if line.startswith(":"):
            words = shlex.split(line)
            if len(words) != 3:
                raise ValueError("Invalid chain declaration")
            if owned_chain(words[0][1:]):
                chains.add(words[0][1:])
        elif line.startswith("-A "):
            words = shlex.split(line)
            if len(words) < 2:
                raise ValueError("Invalid rule")
            rules.append((line, words))
        else:
            raise ValueError("Unexpected filter command")
    if not complete:
        raise ValueError("Incomplete filter table")
    if not chains:
        return None
    deletes = []
    for line, words in rules:
        if words[1] in chains:
            continue
        index = 2
        while index < len(words):
            if words[index] == "--comment":
                index += 2
                continue
            if words[index] in {"-j", "--jump", "-g", "--goto"}:
                if index + 1 >= len(words):
                    raise ValueError("Missing rule target")
                if words[index + 1] in chains:
                    # Retain iptables-save quoting. This is stdin data, never shell input.
                    deletes.append("-D " + line[3:])
                break
            index += 1
    commands = ["*filter", *deletes]
    commands.extend(f"-F {chain}" for chain in sorted(chains))
    commands.extend(f"-X {chain}" for chain in sorted(chains))
    return "\n".join([*commands, "COMMIT", ""])
