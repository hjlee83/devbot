# Task 034 Result: Self-contained Portable Release Artifact

## 완료 내용

- v0.1.0 publish 실패 원인(`ModuleNotFoundError: No module named 'dotenv'`)을 수정했다.
- `build_artifact()`가 `uv.lock`의 DevBot runtime dependency graph를 읽고, 현재 `uv run` 환경에 설치된 exact locked distribution 파일을 `devbot-release/vendor/`에 포함하도록 변경했다.
- launcher가 `PYTHONPATH="$ROOT/vendor:$ROOT/src"`를 설정하고 `python -S`로 실행해 host site-packages에 의존하지 않게 했다.
- launcher가 Python 3.13 interpreter만 선택하도록 `python3.13`, `python3`, `python` 후보를 검증한다.
- clean virtualenv에서 `python-dotenv` 등 runtime dependency가 설치되지 않은 상태로 packaged CLI smoke를 검증했다.

## 주요 설계 결정

- Dependency strategy: prepared virtualenv 전체를 tar에 넣지 않고, `uv.lock` runtime dependency closure만 deterministic vendor directory로 패키징한다.
- `uv.lock`의 `devbot` package dependencies에서 시작해 transitive dependencies를 따라가며 locked package/version을 산출한다.
- 각 dependency는 `importlib.metadata.distribution()`의 installed distribution manifest를 사용해 파일을 복사하고, installed version이 `uv.lock`과 다르면 fail-closed한다.
- tar entry metadata는 기존 artifact builder와 동일하게 mtime/mode를 정규화한다. wheel RECORD의 `../../../bin/...` 같은 site script 경로는 import runtime에 필요하지 않고 extraction escape가 되므로 제외한다.
- Python interpreter 자체는 artifact에 포함하지 않는다. DevBot package contract가 Python 3.13을 요구하므로 launcher는 PATH의 Python 3.13을 검증해 사용하고, runtime packages는 artifact-local `vendor/`만 사용한다.

## 수정 파일

- `src/devbot/release.py`
- `tests/test_release.py`
- `tasks/034-self-contained-portable-artifact.md`
- `results/034-self-contained-portable-artifact.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-034-1 locked dependency strategy | `test_release_artifact_vendors_locked_runtime_dependencies` |
| CP-034-2 artifact-local launcher | `test_packaged_cli_uses_artifact_vendor_before_host_packages` |
| CP-034-3 clean environment smoke | `test_packaged_cli_runs_in_clean_virtualenv_without_runtime_dependencies` |
| CP-034-4 deterministic artifact/checksum | `test_release_artifact_generation_is_reproducible`, `test_checksum_manifest_covers_every_release_artifact`, manual `cmp`, `shasum -a 256 -c SHA256SUMS` |
| CP-034-5 existing behavior preserved | Existing `tests/test_release.py` release planning/notes tests |
| CP-034-6 validation gate | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `UV_CACHE_DIR=/private/tmp/devbot-task034-uv-cache uv run pytest tests/test_release.py -q`: PASS, 40 passed
- `UV_CACHE_DIR=/private/tmp/devbot-task034-uv-cache uv run python scripts/release_pipeline.py build-artifact --version 0.1.0 --os-name portable --architecture python --output-dir /private/tmp/devbot-task034-release-3 --project-root .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task034-uv-cache uv run python scripts/release_pipeline.py checksum-manifest --version 0.1.0 --artifact-dir /private/tmp/devbot-task034-release-3 --output /private/tmp/devbot-task034-release-3/SHA256SUMS`: PASS
- `shasum -a 256 -c SHA256SUMS`: PASS, `devbot-0.1.0-portable-python.tar.gz: OK`
- `PATH=/private/tmp/devbot-task034/.venv/bin:$PATH /private/tmp/devbot-task034-smoke-3/devbot-release/bin/devbot --version`: PASS, `devbot 0.1.0`
- deterministic rebuild `cmp /private/tmp/devbot-task034-release-3/devbot-0.1.0-portable-python.tar.gz /private/tmp/devbot-task034-release-4/devbot-0.1.0-portable-python.tar.gz`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task034-uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task034-uv-cache uv run pytest`: PASS, 562 passed

## 수동 검증 결과

- Artifact: `devbot-0.1.0-portable-python.tar.gz`
- SHA-256: `4f65f82df2219812c5a275475a61e3574c08c5bb20ef6a0dacd53130509709fc`
- `SHA256SUMS` content:

```text
4f65f82df2219812c5a275475a61e3574c08c5bb20ef6a0dacd53130509709fc  devbot-0.1.0-portable-python.tar.gz
```

## 남은 TODO와 제한

- v0.1.0 GitHub Release와 `v0.1.0` tag는 생성하지 않았다.
- PR merge 후 기존 release pipeline으로 publish를 다시 실행해야 한다.
- Artifact는 Python 3.13 interpreter가 PATH에 있는 supported macOS/Linux host를 요구한다. Runtime Python packages는 host에 설치되어 있을 필요가 없다.

## 위험 요소

- Native wheel 파일(예: PyYAML extension)은 artifact를 빌드한 OS/architecture에 맞는다. 현재 supported artifact matrix가 `portable/python` 1개라 workflow runner platform과 supported platform 정책을 계속 일치시켜야 한다.

## Improvement Suggestions

- 향후 macOS/Linux를 동시에 배포하려면 release matrix를 OS/architecture별 artifact로 확장하고 각 runner에서 해당 platform artifact를 빌드해야 한다.
