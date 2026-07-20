# Issue #122 Result: Repository Registration with `devbot init`

## 완료 내용

- 필수 `WORKSPACE_ROOT` 기반 저장소 discovery를, 대상 저장소 내부에서
  실행하는 명시적 등록(`devbot init`)으로 대체했다.
- 새 `devbot.repository_registry`(`src/devbot/repository_registry.py`)가
  두 파일을 의도적으로 분리해서 관리한다:
  - `<repo>/.devbot/config.yaml` - 저장소별 정책
    (owner/repo/enabled/default_branch/automerge_allowed/is_self_repo/
    publish_strategy, `config/repositories.yaml` 항목과 동일한 필드) -
    project 설정이므로 커밋 대상.
  - 전역 registry(`~/.devbot/registry.yaml` 기본값, `DEVBOT_REGISTRY_PATH`
    로 override) - 절대 경로만 저장, 머신마다 다르므로 커밋 대상 아님.
- `devbot init [--owner] [--repo] [--default-branch] [--automerge-allowed]
  [--unregister]`을 추가했다. `load_config()` 호출 전에 early-exit으로
  처리되어 `WORKSPACE_ROOT`/`GITHUB_TOKEN`/`config/repositories.yaml`이
  아직 없어도 동작한다(그 설정을 만드는 게 이 명령의 목적이므로).
  owner/repo는 `--owner`/`--repo` 명시가 없으면 `origin` remote URL(SSH·
  HTTPS 둘 다)에서 추론하고, 추론 불가능하고 명시도 없으면 추측하지 않고
  명시적으로 실패한다.
- `.devbot/config.yaml` 쓰기와 registry 등록 모두 재실행에 멱등이다 -
  변경 없는 재실행은 아무것도 쓰지 않고, 이미 설정된 필드(예:
  `--automerge-allowed`로 한 번 켠 것)를 재실행 시 기본값으로 되돌리지
  않는다.
- registry 파일 갱신은 같은 디렉터리의 임시 파일에 먼저 쓰고 fsync 후
  `os.replace()`로 교체한다. replace 전 실패하면 기존 registry 파일은
  그대로 유지된다.
- registry 등록/해제는 registry path별 advisory lock을 잡은 뒤
  `load_registry()` -> mutate -> atomic replace 전체를 수행한다. 동시에
  여러 `devbot init`/`devbot init --unregister`가 실행되어도 마지막 writer가
  앞선 갱신을 덮어쓰는 lost update를 만들지 않는다.
- `devbot.config.load_config()`가 `WORKSPACE_ROOT`를 선택 사항으로
  바꿨다(`DevBotConfig.workspace_root: Path | None`). legacy(`WORKSPACE_ROOT`
  + `config/repositories.yaml`, `WORKSPACE_ROOT`가 설정된 경우에만 로드-
  기존 배포는 동작 변화 없음)와 registry 두 소스를 합집합으로 관리하고,
  같은 owner/repo가 두 소스에 동시에 있으면 fail closed(`ConfigError`)한다.
  둘 다 비어 있으면 "저장소가 하나도 설정되지 않음" 에러로 명확히
  거부한다.
- registry 쪽의 개별 문제(경로 이동/삭제, 같은 owner/repo 중복 등록,
  손상된 `.devbot/config.yaml`)는 절대 예외를 던지지 않고
  `RegistryDiagnostic`으로 수집한다 - 등록 하나가 깨졌다고 다른 정상
  저장소 관리까지 막지 않는다. `DevBotConfig.registry_diagnostics`로
  노출되고, `devbot doctor`의 새 `repository_registrations` 체크
  (비-fatal, 기존 체크들과 동일한 패턴)로도 확인 가능하다.
- `WorktreeManager.workspace_root`가 클래스 내부에서 실제로는 전혀
  읽히지 않는 필드였음을 확인하고(`worktree_path`/`worktree_root`는
  `repository.local_path`만 사용) 같이 `Path | None`으로 넓혔다 -
  `config.workspace_root`가 `None`인 배포에서도 안전하게 그대로 흘려보낼
  수 있도록.

## 주요 설계 결정

- registry는 절대 경로만 저장하고, 실제 정책(owner/repo/enabled/...)은
  각 저장소 자신의 `.devbot/config.yaml`에서만 읽는다 - Issue #122 본문이
  명시한 "A global registry may store absolute repository locations, while
  repository-local configuration stores only repository-specific policy
  and settings"를 그대로 따른 것이다.
- `devbot init`은 `main()`에서 `--version`과 같은 자리에 early-exit으로
  배선했다 - `load_config()`가 필요로 하는 어떤 설정도 아직 없는 상태가
  이 명령의 정상적인 첫 실행 시나리오이기 때문이다.
- registry 쪽 문제는 "탐지는 하되 절대 다른 저장소를 막지 않는다"는
  원칙으로 처리했다(missing path, 손상된 config는 해당 entry만 skip +
  diagnostic, 같은 owner/repo 중복은 그 identity의 모든 entry 격리 +
  diagnostic). 반면 legacy와 registry 두 소스에 동시에 등록된 같은
  owner/repo는 진짜 모호한 경우라 fail closed했다 - 어느 쪽 설정이
  맞는지 추측할 근거가 없기 때문이다.
- `initialize_repository()`는 재실행 시 기존 `.devbot/config.yaml`을
  먼저 읽어(있다면) 그 값을 기본값으로 삼는다 - 그래야 `--automerge
  -allowed` 없이 재실행해도 이전에 켠 설정이 꺼지지 않는다.

## 수정/추가 파일

- `src/devbot/repository_registry.py` (신규)
- `src/devbot/config.py` (`WORKSPACE_ROOT` 선택 사항화, 두 소스 병합)
- `src/devbot/models.py` (`workspace_root: Path | None`,
  `registry_diagnostics` 필드 추가)
- `src/devbot/main.py` (`devbot init` CLI, early-exit 배선)
- `src/devbot/doctor.py` (`check_repository_registrations`)
- `src/devbot/worktree.py` (`WorktreeManager.workspace_root`를
  `Path | None`으로- 원래도 내부에서 안 쓰이던 필드)
- `tests/test_repository_registry.py` (신규, 21개)
- `tests/test_config.py` (+4, legacy/registry 병합·중복·missing path 회귀)
- `tests/test_main.py` (+8, `devbot init` CLI + 전체 daemon 통합)
- `tests/test_doctor.py` (+3, `repository_registrations` 체크)
- `tests/conftest.py` (`DEVBOT_REGISTRY_PATH`를 매 테스트마다 격리된
  경로로 - 실행 머신의 실제 `~/.devbot/registry.yaml` 상태에 테스트
  결과가 의존하지 않도록)
- `README.md`, `.env.example`, `docs/00-roadmap.md`,
  `docs/19-repository-registration.md`(신규)

## Checkpoint Evidence

- Naming/idempotency/collision: `tests/test_repository_registry.py`
  (`test_register_repository_is_idempotent`,
  `test_register_repository_keeps_existing_registry_when_atomic_replace_fails`,
  `test_concurrent_register_repository_preserves_both_entries`,
  `test_concurrent_register_and_unregister_does_not_lose_updates`,
  `test_register_repository_lock_timeout_preserves_existing_registry`,
  `test_initialize_repository_is_idempotent`,
  `test_initialize_repository_preserves_existing_settings_on_rerun`)
- 필수 metadata 검증(owner/repo 추론 실패): `test_initialize_repository
  _without_remote_or_override_raises`
- moved/deleted/duplicate/invalid registration 진단: `test_resolve
  _registered_repositories_reports_missing_path`,
  `..._reports_duplicate_owner_repo`, `..._reports_invalid_config`
- unregistration: `test_unregister_repository_returns_false_when_not
  _registered`, `test_init_command_unregister_removes_registration`
- legacy `WORKSPACE_ROOT` 호환성: `test_legacy_and_registered_repositories
  _are_merged`, `test_workspace_root_is_optional_when_a_repository_is
  _registered`, 기존 `test_config.py`의 모든 `WORKSPACE_ROOT` 기반 테스트
  변경 없이 통과
- CLI 통합: `tests/test_main.py`의 `test_init_command_*` 8개,
  `test_daemon_run_sees_a_devbot_init_registered_repository`(전체 daemon
  `--once` 경로가 registry 기반 저장소를 인식하는지 end-to-end 확인)

## Validation 결과

```
$ uv run ruff check .
All checks passed!

$ uv run pytest
1338 passed in 109.06s

$ UV_CACHE_DIR=.uv-cache uv run devbot --help
(exit 0, `init` 서브커맨드 노출 확인)
```

## 남은 TODO와 제한

- `devbot init`은 `origin` remote만 확인한다 - 다른 remote 이름을 쓰는
  저장소는 `--owner`/`--repo`를 명시해야 한다.
- registry/local config 파일 형식 마이그레이션(스키마 버전 등)은 다루지
  않는다 - 첫 배포이므로 아직 필요하지 않다.

## 범위 밖 (Issue #122 Non-goals 그대로)

Goal/Task/verification-gate 재설계, implementer/reviewer/rework role 선택
변경, repository-map이나 자동 context 선택 기능, GitHub Release나 패키지
배포 변경, 명시적 등록 이전의 임의 상위 디렉터리 자동 스캔,
`WORKSPACE_ROOT` 호환성 제거 - 전부 이번 작업에 포함하지 않았다.
