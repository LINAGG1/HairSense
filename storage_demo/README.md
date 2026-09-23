# Storage schema v1

신규 DB는 `setup.sql`을 실행합니다. 기존 테이블은 `CREATE TABLE IF NOT EXISTS`로 변경되지 않습니다.

기존 DB 변경 순서:
1. `sensor_readings`를 백업하고 API 및 모든 쓰기 작업을 중단합니다.
2. MySQL Workbench에서 관리자 계정으로 `migrate_boot_id_v1.sql` 전체를 한 번 실행합니다.
3. 출력된 테이블 정의에서 `boot_id NOT NULL`, 정수 `schema_version`, 기본 키 `(device_id, session_id, boot_id, seq)`를 확인하고 수정된 API를 재시작합니다.

마이그레이션은 기존 버전 문자열 `0.1`과 `1`만 정수 `1`로 정규화합니다. 그 외 값이 있으면 변경 전에 중단합니다. 기존 행의 `boot_id`는 `legacy-unknown`입니다. 이는 실제 부팅 정보를 복구한 값이 아니며, 과거 행이 같은 부팅에서 발생했다는 보장도 없습니다. 새 부팅 ID로 이 예약 값을 사용하지 마세요. 원래 버전 값은 백업에서 확인할 수 있습니다. 타임스탬프와 센서 값은 변환하지 않습니다.

MySQL DDL은 암묵적으로 커밋됩니다. 실패하면 부분 적용 여부를 확인하고 필요하면 백업으로 복원해야 합니다. 무조건 다시 실행하지 마세요.

`GET /readings`에는 `device_id`, `session_id`, `boot_id`가 모두 필요합니다. `after_seq` 페이지네이션은 해당 부팅 안에서만 진행합니다. 전처리 입력은 메타데이터의 단일 `boot_id`와 일치해야 하며, 부팅별 캡처 시간 범위가 필요합니다.
