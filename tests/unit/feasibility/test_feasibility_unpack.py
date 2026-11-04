import io
import tarfile
from pathlib import Path

import pytest
from scripts.feasibility_support.unpack import (
    UnsafeLayerError,
    apply_directory_modes,
    extract_layer,
)


def _archive(*entries: tuple[str, bytes | str | None, bytes | None]) -> tarfile.TarFile:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, target, content in entries:
            member = tarfile.TarInfo(name)
            if isinstance(target, str):
                member.type = tarfile.SYMTYPE
                member.linkname = target
            elif target is None and content is None:
                member.type = tarfile.DIRTYPE
                member.mode = 0o755
            elif target is None:
                member.type = tarfile.REGTYPE
                member.size = len(content or b"")
                archive.addfile(member, io.BytesIO(content or b""))
                continue
            else:
                member.type = tarfile.LNKTYPE
                member.linkname = target.decode()
            archive.addfile(member)
    stream.seek(0)
    return tarfile.open(fileobj=stream, mode="r:")


@pytest.mark.parametrize("name", ["/outside", "../outside", "opt/../../outside"])
def test_absolute_and_relative_member_paths_are_rejected(tmp_path: Path, name: str) -> None:
    root = tmp_path / "rootfs"
    root.mkdir()
    with (
        _archive((name, None, b"payload")) as archive,
        pytest.raises(UnsafeLayerError, match="unsafe layer path"),
    ):
        extract_layer(archive, root, {})
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize(
    ("target", "contained_path"),
    [
        ("/tmp/absolute-escape", "tmp/absolute-escape/payload"),  # noqa: S108
        ("../../relative-escape", "relative-escape/payload"),
    ],
)
def test_symlink_targets_use_image_root_semantics(
    tmp_path: Path, target: str, contained_path: str
) -> None:
    root = tmp_path / "rootfs"
    root.mkdir()
    with _archive(
        ("opt", target, None),
        ("opt/payload", None, b"inside image root"),
    ) as archive:
        extract_layer(archive, root, {})
    assert (root / contained_path).read_bytes() == b"inside image root"
    assert not (tmp_path / "absolute-escape").exists()
    assert not (tmp_path / "relative-escape").exists()


@pytest.mark.parametrize("target", ["/outside", "../../outside"])
def test_absolute_and_relative_hardlink_escapes_are_rejected(tmp_path: Path, target: str) -> None:
    root = tmp_path / "rootfs"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"host sentinel")
    with (
        _archive(("copy", target.encode(), None)) as archive,
        pytest.raises(UnsafeLayerError, match="unsafe layer path"),
    ):
        extract_layer(archive, root, {})
    assert outside.read_bytes() == b"host sentinel"
    assert not (root / "copy").exists()


def test_safe_symlinks_hardlinks_and_whiteouts_keep_layer_semantics(tmp_path: Path) -> None:
    root = tmp_path / "rootfs"
    root.mkdir()
    directory_modes: dict[tuple[str, ...], int] = {}
    with _archive(
        ("usr", None, None),
        ("usr/bin", None, None),
        ("usr/bin/tool", None, b"tool"),
        ("usr/bin/obsolete", None, b"old layer"),
        ("bin", "usr/bin", None),
    ) as archive:
        extract_layer(archive, root, directory_modes)
    with _archive(
        ("bin/copy", b"usr/bin/tool", None),
        ("usr/bin/.wh.obsolete", None, b""),
    ) as archive:
        extract_layer(archive, root, directory_modes)
    assert (root / "bin/copy").stat().st_ino == (root / "usr/bin/tool").stat().st_ino
    assert not (root / "usr/bin/obsolete").exists()
    with _archive(
        ("usr/bin/.wh..wh..opq", None, b""),
        ("usr/bin/tool", None, b"tool after opaque whiteout"),
        ("usr/bin/new", None, b"new layer"),
    ) as archive:
        extract_layer(archive, root, directory_modes)
    apply_directory_modes(root, directory_modes)
    assert (root / "bin/tool").read_bytes() == b"tool after opaque whiteout"
    assert not (root / "bin/copy").exists()
    assert not (root / "usr/bin/obsolete").exists()
    assert (root / "usr/bin/new").read_bytes() == b"new layer"


def test_final_directory_modes_do_not_follow_replaced_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "rootfs"
    root.mkdir()
    target = root / "target"
    target.mkdir(mode=0o700)
    (root / "alias").symlink_to(target, target_is_directory=True)

    apply_directory_modes(root, {("alias",): 0})

    assert target.stat().st_mode & 0o777 == 0o700
