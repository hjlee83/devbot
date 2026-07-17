#!/usr/bin/env python3
"""Local entrypoint used by the GitHub release workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from devbot.release import (
    SUPPORTED_PLATFORMS,
    Artifact,
    PullRequestMetadata,
    ReleaseRecord,
    authoritative_version,
    build_artifact,
    checksum_manifest,
    expected_artifact_names,
    manual_release_plan,
    release_plan_for_pr,
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


def _load_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _release_record(payload: dict[str, object]) -> ReleaseRecord:
    return ReleaseRecord(
        tag_name=str(payload["tag_name"]),
        target_commitish=str(payload["target_commitish"]),
        draft=bool(payload.get("draft", False)),
        prerelease=bool(payload.get("prerelease", False)),
    )


def _pr_metadata(payload: dict[str, object]) -> PullRequestMetadata:
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ValueError("push release planning requires a pull_request payload")
    base = pull_request.get("base")
    if not isinstance(base, dict):
        raise ValueError("pull_request.base is required")
    labels = pull_request.get("labels", [])
    if not isinstance(labels, list):
        raise ValueError("pull_request.labels must be a list")
    merged = bool(
        pull_request.get("merged", False)
        or pull_request.get("merged_at") is not None
        or pull_request.get("state") == "closed"
    )
    return PullRequestMetadata(
        number=int(pull_request["number"]),
        title=str(pull_request["title"]),
        labels=tuple(str(label["name"]) for label in labels if isinstance(label, dict)),
        merged=merged,
        base_ref=str(base["ref"]),
        merge_commit_sha=str(pull_request["merge_commit_sha"]),
    )


def _write_plan_outputs(args: argparse.Namespace, plan: object) -> None:
    payload = {
        "publish": getattr(plan, "publish"),
        "previous_version": getattr(plan, "previous_version"),
        "increment": getattr(plan, "increment") or "",
        "version": getattr(plan, "new_version"),
        "tag": getattr(plan, "tag"),
        "notes": getattr(plan, "notes"),
        "reason": getattr(plan, "reason"),
    }
    Path(args.output).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    github_output = args.github_output
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as stream:
            for key, value in payload.items():
                if key == "notes":
                    stream.write(f"{key}<<EOF\n{value}\nEOF\n")
                else:
                    rendered = str(value).lower() if isinstance(value, bool) else value
                    stream.write(f"{key}={rendered}\n")


def _plan(args: argparse.Namespace) -> int:
    releases_payload = _load_json(args.releases_json)
    if not isinstance(releases_payload, list):
        raise ValueError("releases JSON must be a list")
    releases = tuple(
        _release_record(release)
        for release in releases_payload
        if isinstance(release, dict)
    )
    main_commits_payload = _load_json(args.main_commits_json)
    if not isinstance(main_commits_payload, list):
        raise ValueError("main commits JSON must be a list")
    main_commits = {str(commit) for commit in main_commits_payload}

    if args.event_name == "workflow_dispatch":
        plan = manual_release_plan(
            increment=args.increment,
            releases=releases,
            main_commits=main_commits,
            initial_version=args.initial_version,
        )
    else:
        event_payload = _load_json(args.event_json)
        if not isinstance(event_payload, dict):
            raise ValueError("event JSON must be an object")
        plan = release_plan_for_pr(
            _pr_metadata(event_payload),
            releases=releases,
            main_commits=main_commits,
            initial_version=args.initial_version,
        )
    _write_plan_outputs(args, plan)
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

    plan = subparsers.add_parser("plan")
    plan.add_argument("--event-name", required=True, choices=["push", "workflow_dispatch"])
    plan.add_argument("--event-json", required=True)
    plan.add_argument("--releases-json", required=True)
    plan.add_argument("--main-commits-json", required=True)
    plan.add_argument("--initial-version", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--github-output")
    plan.add_argument("--increment", choices=["patch", "minor", "major"])
    plan.set_defaults(func=_plan)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
