"""Unpack verified OCI layers inside the restricted staging namespace only."""

import json
import pathlib
import shutil
import tarfile

layers = pathlib.Path("/layers")
root = pathlib.Path("/rootfs")
manifest = json.loads((layers / "manifest.json").read_text())
for layer in manifest["layers"]:
    with tarfile.open(layers / layer["digest"][7:]) as archive:
        members = archive.getmembers()
        for member in members:
            name = pathlib.PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeError("unsafe layer path")
            if name.name.startswith(".wh."):
                if name.name == ".wh..wh..opq":
                    raise RuntimeError("opaque whiteout not supported")
                target = root.joinpath(*name.parent.parts, name.name[4:])
                if not target.parent.resolve(strict=False).is_relative_to(root):
                    raise RuntimeError("whiteout escapes root")
                if target.is_symlink() or target.is_file():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
        archive.extractall(  # noqa: S202  # Digest-verified layers in a write-restricted sandbox.
            root,
            members=[
                m for m in members if not pathlib.PurePosixPath(m.name).name.startswith(".wh.")
            ],
            numeric_owner=False,
            filter="fully_trusted",
        )
