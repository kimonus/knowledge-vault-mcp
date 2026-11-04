"""Apply verified OCI layers without allowing paths to escape the image root."""

import json
import os
import pathlib
import shutil
import stat
import tarfile
from collections.abc import MutableMapping


class UnsafeLayerError(RuntimeError):
    """An OCI layer contains an unsupported or unsafe filesystem entry."""


def _parts(name: str) -> tuple[str, ...]:
    path = pathlib.PurePosixPath(name)
    if "\0" in name or path.is_absolute() or ".." in path.parts:
        raise UnsafeLayerError("unsafe layer path")
    return path.parts


def _rooted_path(
    root: pathlib.Path, parts: tuple[str, ...], *, follow_leaf: bool = True
) -> pathlib.Path:
    """Resolve image-root symlinks as container paths, never as host absolute paths."""
    pending = list(parts)
    resolved: list[str] = []
    links = 0
    index = 0
    missing_depth: int | None = None
    while index < len(pending):
        part = pending[index]
        if part in ("", "."):
            index += 1
            continue
        if part == "..":
            if resolved:
                resolved.pop()
            if missing_depth is not None and len(resolved) < missing_depth:
                missing_depth = None
            index += 1
            continue
        if missing_depth is not None:
            resolved.append(part)
            index += 1
            continue
        candidate = root.joinpath(*resolved, part)
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            resolved.append(part)
            missing_depth = len(resolved)
            index += 1
            continue
        is_leaf = index == len(pending) - 1
        if stat.S_ISLNK(info.st_mode) and (follow_leaf or not is_leaf):
            links += 1
            if links > 40:
                raise UnsafeLayerError("too many symbolic links in layer path")
            target = pathlib.PurePosixPath(os.readlink(candidate))
            if target.is_absolute():
                resolved = []
            target_parts = target.parts[1:] if target.is_absolute() else target.parts
            pending = [*target_parts, *pending[index + 1 :]]
            index = 0
            missing_depth = None
            continue
        resolved.append(part)
        index += 1
    return root.joinpath(*resolved)


def _ensure_directory(root: pathlib.Path, parts: tuple[str, ...]) -> pathlib.Path:
    path = _rooted_path(root, parts)
    try:
        info = path.stat()
    except FileNotFoundError:
        path.mkdir(parents=True, mode=0o700)
    else:
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafeLayerError("layer path parent is not a directory")
    return path


def _ensure_image_directory(root: pathlib.Path, parts: tuple[str, ...]) -> pathlib.Path:
    if not parts:
        return root
    parent = _ensure_directory(root, parts[:-1])
    path = parent / parts[-1]
    try:
        info = path.lstat()
    except FileNotFoundError:
        path.mkdir(mode=0o700)
    else:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            _remove(path)
            path.mkdir(mode=0o700)
    return path


def _remove(path: pathlib.Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _forget_directory_modes(
    root: pathlib.Path,
    modes: MutableMapping[tuple[str, ...], int],
    path: pathlib.Path,
) -> None:
    parts = path.relative_to(root).parts
    for candidate in tuple(modes):
        if candidate[: len(parts)] == parts:
            modes.pop(candidate)


def _apply_whiteouts(
    archive: tarfile.TarFile,
    root: pathlib.Path,
    directory_modes: MutableMapping[tuple[str, ...], int],
) -> set[str]:
    markers: set[str] = set()
    for member in archive.getmembers():
        parts = _parts(member.name)
        if not parts or not parts[-1].startswith(".wh."):
            continue
        if not (
            member.isreg() or (member.ischr() and member.devmajor == 0 and member.devminor == 0)
        ):
            raise UnsafeLayerError("invalid OCI whiteout entry")
        target_name = parts[-1][4:]
        if parts[-1] != ".wh..wh..opq" and target_name in ("", ".", ".."):
            raise UnsafeLayerError("invalid OCI whiteout target")
        markers.add(member.name)
        parent = _rooted_path(root, parts[:-1])
        if not parent.is_dir():
            continue
        if parts[-1] == ".wh..wh..opq":
            for child in parent.iterdir():
                _forget_directory_modes(root, directory_modes, child)
                _remove(child)
        else:
            target = parent / target_name
            _forget_directory_modes(root, directory_modes, target)
            _remove(target)
    return markers


def extract_layer(
    archive: tarfile.TarFile,
    root: pathlib.Path,
    directory_modes: MutableMapping[tuple[str, ...], int],
) -> None:
    """Extract regular files, directories and links with OCI-root path semantics."""
    markers = _apply_whiteouts(archive, root, directory_modes)
    for member in archive.getmembers():
        if member.name in markers:
            continue
        parts = _parts(member.name)
        if not parts:
            if member.isdir():
                continue
            raise UnsafeLayerError("unsupported image-root entry")
        parent = _ensure_directory(root, parts[:-1])
        path = parent / parts[-1]
        if member.isdir():
            try:
                info = path.lstat()
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    _forget_directory_modes(root, directory_modes, path)
            _ensure_image_directory(root, parts)
            canonical = _ensure_directory(root, parts).relative_to(root).parts
            directory_modes[canonical] = member.mode & 0o777
        elif member.issym():
            if not member.linkname or "\0" in member.linkname:
                raise UnsafeLayerError("invalid symbolic link target")
            _forget_directory_modes(root, directory_modes, path)
            _remove(path)
            os.symlink(member.linkname, path)
        elif member.islnk():
            link_parts = _parts(member.linkname)
            if not link_parts:
                raise UnsafeLayerError("invalid hardlink target")
            source = _rooted_path(root, link_parts, follow_leaf=False)
            source_info = source.lstat()
            if not stat.S_ISREG(source_info.st_mode):
                raise UnsafeLayerError("hardlink target is not a regular file")
            _forget_directory_modes(root, directory_modes, path)
            _remove(path)
            os.link(source, parent / parts[-1], follow_symlinks=False)
        elif member.isreg():
            source = archive.extractfile(member)
            if source is None:
                raise UnsafeLayerError("regular file has no data")
            _forget_directory_modes(root, directory_modes, path)
            _remove(path)
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                member.mode & 0o777,
            )
            with source, os.fdopen(descriptor, "wb") as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fchmod(output.fileno(), member.mode & 0o777)
        else:
            raise UnsafeLayerError("unsupported special file in OCI layer")


def apply_directory_modes(root: pathlib.Path, modes: MutableMapping[tuple[str, ...], int]) -> None:
    """Apply final layer directory permissions only after all layers are written."""
    for parts, mode in sorted(modes.items(), key=lambda item: len(item[0]), reverse=True):
        path = root
        for component in parts:
            path = path / component
            try:
                info = path.lstat()
            except FileNotFoundError:
                break
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                break
        else:
            path.chmod(mode)


def unpack_layers(layers: pathlib.Path, root: pathlib.Path) -> None:
    manifest = json.loads((layers / "manifest.json").read_text())
    directory_modes: dict[tuple[str, ...], int] = {}
    for layer in manifest["layers"]:
        name = layer["digest"][7:]
        with tarfile.open(layers / name, mode="r:*") as archive:
            extract_layer(archive, root, directory_modes)
    apply_directory_modes(root, directory_modes)


if __name__ == "__main__":
    unpack_layers(pathlib.Path("/layers"), pathlib.Path("/rootfs"))
