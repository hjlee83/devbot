# Task 001 Improvement Suggestions

## 애매했던 요구사항

- `WORKSPACE_ROOT`가 `.env`(환경 변수)에서 오는지 `config/repositories.yaml`에서
  오는지 Task 문서에 명시되어 있지 않았다. "workspace_root / repo"로 로컬
  경로를 계산하라고만 되어 있음. macOS/Linux VPS 간 이식성을 고려해
  `.env`(호스트별 값)에 두고, 저장소 목록은 `repositories.yaml`(git에 커밋되는
  값)에 두는 것으로 해석해 구현했다.
- "validate enabled repository paths"가 정확히 무엇을 검증해야 하는지
  (디렉터리 존재 여부만인지, `.git` 존재까지 확인하는 git 저장소 검증인지)
  불명확했다. 이번 Task는 `is_dir()`만 확인하는 최소 구현으로 좁혔다.
- "print the managed repositories"가 활성화(enabled)된 저장소만 뜻하는지,
  비활성화된 것까지 전부 나열해야 하는지 불명확했다. 검증까지 통과한
  enabled 저장소만 출력하도록 구현했다.
- 저장소에 git 커밋이 전혀 없는 상태(unborn main)에서 시작했다. "main 직접
  커밋/push 금지" 규칙을 문자 그대로 지키면 첫 커밋 자체가 불가능해지는
  모순이 있어, 사용자에게 직접 확인 후 "기존 스캐폴드 파일만 main에 1회
  초기 커밋 → Task 001 구현은 feature 브랜치"로 처리했다. 이 예외 처리
  방식을 앞으로 반복될 초기 부트스트랩 상황에 대비해 AGENTS.md 등에
  명문화해두면 좋겠다.

## 추가하면 좋을 품질 게이트

- `uv run devbot`을 CI 등 새 환경에서 그대로 실행했을 때도 통과하려면
  `WORKSPACE_ROOT`가 필요하다는 점이 문서화되어 있지 않다. `.env`가 없는
  상태에서 `uv run devbot`을 실행하면 (의도된 대로) 에러로 종료되므로,
  "검증 명령이 로컬 `.env` 존재를 전제로 한다"는 점을 Task 문서나 README에
  명시하는 게이트를 추가하면 재현성 문제를 줄일 수 있다.
- `uv sync`가 `requires-python`을 만족하는 가장 최신 인터프리터(예:
  3.14)를 임의로 선택할 수 있다는 점을 이번에 확인했다(`.python-version`
  또는 상한 버전 지정이 없으면 3.13이 아닌 3.14가 선택됨). "Python 3.13
  고정"을 실제로 강제하는지 확인하는 게이트(`uv run python --version`
  체크 등)를 검증 스크립트에 추가하면 좋겠다.

## 누락된 경계 조건

- Lock 파일 부모 디렉터리가 없을 때의 동작(자동 생성)은 이번 구현에서
  다뤘지만 Task에는 명시되어 있지 않았다.
- (해결됨) 최초 구현에서는 "같은 프로세스 내 두 번째 열린 파일 디스크립터"로
  락 거부를 테스트했었다. 리뷰 피드백을 반영해 `tests/test_lock.py`의
  `test_lock_rejects_second_owner`를 `multiprocessing`으로 실제 자식 OS
  프로세스를 spawn해 락 획득을 시도하는 방식으로 교체했다.
- (해결됨) 최초 구현에서는 `config.py`의 `enabled: bool(entry.get(...))`가
  YAML에 `enabled: "false"`(따옴표로 감싼 문자열)처럼 적혀 있어도 Python의
  문자열 truthy 규칙 때문에 `True`로 잘못 해석되는 버그가 있었다. 리뷰
  피드백을 반영해 `_parse_repository_enabled`로 교체하고
  `enabled: "false"` 회귀 테스트를 추가했다. 같은 리뷰에서 DRY_RUN 등 다른
  "invalid required value" 케이스(빈 문자열, 알 수 없는 boolean 문자열,
  잘못된 YAML)에 대한 테스트도 CP-001-2 보강으로 추가했다.

## 다음 Task에 반영할 제안

- Task 002(GitHub read client)에서 `queue.py`의 `IssueTask`를 실제 GitHub
  Issue/label 데이터로 채우는 매핑 함수의 소유권(어느 모듈이 변환 책임을
  갖는지)을 Task 문서에 명시해주면 좋겠다.
- `config/repositories.yaml`의 스키마(특히 `enabled` 기본값, 우선순위
  라벨과의 관계)를 `docs/05-task-format.md`나 별도 스키마 문서에 고정해두면
  이후 Task에서 동일한 파일을 계속 다시 해석하지 않아도 된다.
