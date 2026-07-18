# Task 034 Result: Self-contained Platform Release Artifacts

## 완료 내용

- v0.1.0 publish 실패 원인(`ModuleNotFoundError: No module named 'dotenv'`)을 수정했다.
- 단일 portable artifact 계약을 platform-specific artifact 2개로 교체했다:
  `devbot-<version>-linux-x86_64.tar.gz`, `devbot-<version>-macos-arm64.tar.gz`.
- release workflow의 `build-artifacts` job을 matrix로 변경했다:
  `ubuntu-latest / linux / x86_64`, `macos-14 / macos / arm64`.
- 각 matrix runner가 자기 platform artifact를 빌드하고 `devbot --version` smoke를 수행한다.
- publish job은 두 artifact를 모두 다운로드한 뒤 하나의 `SHA256SUMS`를 생성한다.
- `build_artifact()`가 `uv.lock` runtime dependency graph를 읽고 exact locked distribution 파일을 `devbot-release/vendor/`에 포함하도록 변경했다.
- launcher는 `PYTHONPATH="$ROOT/vendor:$ROOT/src"`와 `python -S`를 사용해 host site-packages에 의존하지 않는다.
- v0.1.0 source commit은 고정 SHA가 아니라 이 PR merge 후 생성되는 최종 `main` commit으로 문서화했다.

## 주요 설계 결정

- Dependency strategy: prepared virtualenv 전체가 아니라 `uv.lock` runtime dependency closure를 deterministic `vendor/` directory로 패키징한다.
- Platform strategy: native wheel이 OS/architecture에 종속되므로 Linux x86_64와 macOS arm64 artifact를 각각 matching GitHub runner에서 빌드하고 smoke-test한다.
- `uv.lock`과 installed distribution version이 다르면 artifact build는 fail-closed한다.
- wheel RECORD의 `../../../bin/...` 같은 site script 경로는 import runtime에 필요하지 않고 extraction escape가 되므로 제외한다.
- Python interpreter 자체는 artifact에 포함하지 않는다. Launcher는 PATH의 Python 3.13을 검증해 사용하고, runtime packages는 artifact-local `vendor/`만 사용한다.

## 수정 파일

- `.github/workflows/release.yml`
- `src/devbot/release.py`
- `tests/test_release.py`
- `docs/history.md`
- `tasks/032-automated-release-pipeline.md`
- `tasks/033-bootstrap-initial-release-and-project-history.md`
- `tasks/034-self-contained-portable-artifact.md`
- `results/032-automated-release-pipeline.md`
- `results/033-bootstrap-initial-release-and-project-history.md`
- `results/034-self-contained-portable-artifact.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-034-1 locked dependency strategy | `test_release_artifact_vendors_locked_runtime_dependencies` |
| CP-034-2 artifact-local launcher | `test_packaged_cli_uses_artifact_vendor_before_host_packages` |
| CP-034-3 clean environment smoke | `test_packaged_cli_runs_in_clean_virtualenv_without_runtime_dependencies` |
| CP-034-4 platform artifact/checksum | `test_release_artifact_names_are_deterministic`, `test_release_workflow_uses_platform_artifact_matrix_and_manual_dispatch`, `test_checksum_manifest_covers_every_release_artifact` |
| CP-034-5 deterministic artifact | `test_release_artifact_generation_is_reproducible`, manual `cmp` |
| CP-034-6 validation gate | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- Platform artifact builds: PASS for `linux/x86_64` and `macos/arm64`
- `shasum -a 256 -c SHA256SUMS`: PASS for both artifacts
- extracted `devbot-release/bin/devbot --version`: PASS, `devbot 0.1.0` for both artifacts
- Workflow matrix parse: PASS, `ubuntu-latest / linux / x86_64`, `macos-14 / macos / arm64`
- `UV_CACHE_DIR=/private/tmp/devbot-task034-uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task034-uv-cache uv run pytest`: PASS, 562 passed

## 수동 검증 결과

Expected final release assets after merge and workflow dispatch:

- `devbot-0.1.0-linux-x86_64.tar.gz`
- `devbot-0.1.0-macos-arm64.tar.gz`
- `SHA256SUMS` covering both artifacts

Local representative SHA256SUMS:

```text
bc57c75c693eede14f4253ccc810000907961ebb138c65577504f25624c373bd  devbot-0.1.0-linux-x86_64.tar.gz
2e4f230998de3014525abcf80f185bd41652e6733689c0f1e001d8fd2af95226  devbot-0.1.0-macos-arm64.tar.gz
```

v0.1.0 GitHub Release와 `v0.1.0` tag는 생성하지 않았다.

## 남은 TODO와 제한

- PR merge 후 release workflow를 최종 `main` commit으로 dispatch해야 한다.
- GitHub-hosted runners에서 Linux x86_64와 macOS arm64 artifact 각각의 native dependency smoke를 최종 확인해야 한다.

## 위험 요소

- Native wheel 파일(예: PyYAML extension)은 빌드 runner platform에 맞으므로, matrix와 artifact naming contract가 계속 일치해야 한다.
