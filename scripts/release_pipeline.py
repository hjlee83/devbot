#!/usr/bin/env python3
"""Local entrypoint used by the GitHub release workflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from devbot.release import (
    SUPPORTED_PLATFORMS,
    Artifact,
    authoritative_version,
    build_artifact,
    checksum_manifest,
    expected_artifact_names,
)


def _build(args: argparse.Namespace) -> int:
    build_artifact(
        Path(args.output_dir),
        version=args.version,
        os_name=args.os_name,
        architecture=args.architecture,
    )
    return 0


def _manifest(args: argparse.Namespace) -> int:
    output_dir = Path(args.artifact_dir)
    artifacts = []
    names = expected_artifact_names(args.version)
    for index, (os_name, architecture) in enumerate(SUPPORTED_PLATFORMS):
        artifacts.append(
            Artifact(
                name=names[index],
                path=output_dir / names[index],
                os_name=os_name,
                architecture=architecture,
            )
        )
    manifest = checksum_manifest(artifacts, expected_names=names)
    Path(args.output).write_text(manifest, encoding="utf-8")
    return 0


def _version(args: argparse.Namespace) -> int:
    print(authoritative_version(args.project_root))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    version = subparsers.add_parser("version")
    version.add_argument("--project-root", default=".")
    version.set_defaults(func=_version)

    build = subparsers.add_parser("build-artifact")
    build.add_argument("--version", required=True)
    build.add_argument("--os-name", required=True)
    build.add_argument("--architecture", required=True)
    build.add_argument("--output-dir", required=True)
    build.set_defaults(func=_build)

    manifest = subparsers.add_parser("checksum-manifest")
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--artifact-dir", required=True)
    manifest.add_argument("--output", required=True)
    manifest.set_defaults(func=_manifest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
