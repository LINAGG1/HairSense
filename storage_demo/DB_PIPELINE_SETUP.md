# DB 기반 자동 분석 실행

구현 완료, 실제 MySQL 적용 및 실제 Gemini 호출은 아래 순서로 사용자가 확인합니다.
로컬 가상 데이터 전용이며 이미지 분석 함수는 변경하지 않았습니다.

## 1. 서버 종료 후 테이블 추가

서버 터미널에서 Ctrl+C를 누릅니다.
Workbench에서 migration_sensor_pipeline.sql을 관리자 연결로 열고 전체 실행합니다.
현재 hairsense_demo의 sensor_readings 데이터는 보존되고 두 개의 테이블만 추가됩니다.
앱 DB 계정이 hairsense_app@localhost가 아니라면 SQL 마지막 GRANT의 계정을 실제 계정에 맞춥니다.
테이블을 만들려면 관리자 권한이 필요하며 앱 계정 비밀번호를 변경할 필요는 없습니다.
setup.sql을 다시 실행하지 않습니다.

## 2. 서버 재시작

기존 Gemini 키를 설정했던 storage_demo 터미널에서:

```powershell
..\.venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

새 터미널에서는 GEMINI_SETUP.md에 따라 키 환경변수를 먼저 설정합니다.
신규 수신 API도 상태 테이블을 사용하므로 migration 적용 전에는 데이터 전송하지 않습니다.

## 3. 이미 저장한 multi-session-102 완료 처리

다른 터미널의 storage_demo에서:

```powershell
..\.venv\Scripts\python.exe complete_and_verify.py
```

이미 3,000개를 저장했으므로 재전송할 필요가 없습니다.
이 도구는 원본을 전송하지 않고 완료 POST → 동일 POST 재호출 → 결과 GET을 수행합니다.
첫 요청만 DB 분석과 Gemini 호출을 수행하며, 두 번째 요청은 저장된 결과를 반환합니다.
provider 요청에는 기존 요약의 수치와 관측 방향만 포함됩니다. Gemini 과금/할당량이 적용될 수 있습니다.
기대 결과:

```text
PASS: DB analysis, cached completion and result lookup match
first_request_cached: False
optical: flagged=6/30
gyro: flagged=3/30
feedback_source: gemini
fallback_reason: None
```

재실행하면 first_request_cached=True입니다. fallback도 저장되므로 Gemini 실패 시 재실행만으로 재호출하지 않습니다.
PASS는 DB 결과와 반복 조회의 일치이고, Gemini 성공은 feedback_source=gemini로 별도 확인합니다.

## Swagger 직접 호출

새 API `POST /sensor-sessions/multi-session-102/complete`의 요청 본문:

```json
{
  "user_id": "mock-multi-user-001",
  "device_id": "mock-multi-device-001",
  "boot_id": "multi-boot-102",
  "start_timestamp_ms": 1000,
  "is_synthetic": true,
  "schema_version": 1,
  "duration_seconds": 60,
  "sample_rate_hz": 50,
  "interval_ms": 20,
  "optical_unit": "V",
  "gyro_unit": "deg/s"
}
```

`GET /sensor-sessions/multi-session-102/analysis`에는 같은 user_id/device_id/boot_id를 조회 인자로 전달합니다.
분석 품질, 특징 30개 구간, 센서별 점수 60행, 요약, 모델/정책/입력 해시 및 feedback을 확인할 수 있습니다.
기존 `/sensor-analysis/...` 경로는 파일 기반 프리뷰로 유지됩니다. 기존 feedback POST는 캐시가 없으므로 새 흐름 검증에는 사용하지 않습니다.

## 중복·실패 처리

- 수신과 완료는 같은 MySQL 세션 잠금을 사용하며 진행 중 중복 요청은 409 session_busy를 반환합니다.
- 완료된 세션은 봉인됩니다. 동일 세션의 배치 전송은 중복 여부와 관계없이 409 session_sealed를 반환합니다.
- 현재 버전은 세션/boot당 분석 결과 하나를 저장합니다. 모델 파일을 바꿔도 완료 요청 재전송은 기존 결과를 반환합니다.
- 완료 메타데이터 변경/사용자 불일치는 409로 거절합니다. GET은 사용자 불일치 시 404입니다. 이는 로컬 일관성 검사이며 사용자 인증을 대체하지 않습니다.
- 60초/단일 boot/누락 없는 가상 데이터만 안내를 생성합니다. 누락/null이면 insufficient_data와 품질 결과를 저장하며 Gemini를 호출하지 않습니다.
- analysis_failed / feedback_failed 결과도 보존합니다. 재실행으로 실패한 외부 호출을 자동 반복하지 않습니다.
- 서버 종료 등으로 processing / feedback_pending에 남으면 완료 재요청은 409 recovery_required를 반환합니다. GET으로 남아 있는 분석 결과를 조회할 수 있습니다.
- feedback_pending은 외부 호출이 실제 성공했을 수도 있는 상태입니다. 자동 재호출하거나 행을 삭제하지 말고 원인을 먼저 확인합니다.
- 데이터 부족 등으로 봉인된 세션을 재시험하려면 새 가상 session_id/boot_id로 시작합니다. 현재 버전은 재개·강제 재분석 API를 제공하지 않습니다.

## 저장된 fallback 안내만 재생성

새 SQL 적용 없이 서버 코드 갱신/재시작 후 Swagger에서
`POST /sensor-sessions/{session_id}/feedback/retry`를 사용합니다.
session_id는 `multi-session-102`, 요청 본문은 다음과 같습니다.

```json
{
  "user_id": "mock-multi-user-001",
  "device_id": "mock-multi-device-001",
  "boot_id": "multi-boot-102",
  "retry_id": "feedback-retry-001"
}
```

저장된 요약으로 Gemini를 한 번 호출하고 새 안내를 DB에 저장합니다. 특징·점수·요약·모델은 재계산하지 않습니다.
이전 안내와 각 재시도 결과는 기존 result_json의 feedback_retries에 보존합니다.
같은 retry_id는 결과를 재사용합니다. HTTP 응답을 받지 못한 경우에도 같은 ID로 확인합니다.
새 결과가 fallback이면 같은 ID를 반복해도 추가 호출하지 않습니다.
원인을 확인한 뒤 명시적으로 새로 시도하려면 retry_id를 바꿉니다. 세션당 최대 20회로 제한합니다.
이미 Gemini 안내가 저장돼 있으면 새 ID로 요청해도 status=already_gemini, cached=true로 반환합니다.
호출 전 pending 상태를 커밋합니다. 도중에 중단되면 새 ID도 409로 거절하여 불확실한 외부 호출을 반복하지 않습니다.
예외 발생 시 이전 안내는 유지하고 시도 실패를 기록합니다.
다른 사용자, 미완료/데이터 부족 세션, 요약 없는 세션은 재생성할 수 없습니다.
실제 호출 과금/할당량이 적용될 수 있으며, user_id 확인은 로컬 일관성 검사이지 인증이 아닙니다.
GET analysis로 최신 feedback과 이력을 확인합니다. 기존 complete 검증 도구를 다시 실행해도 새 저장 결과를 조회할 수 있습니다.

## 검증 명령

```powershell
..\.venv\Scripts\python.exe -m unittest test_sensor_pipeline test_sensor_feedback test_send_and_verify -v
```

고정 모델의 기존 파일 요약 재현, 결측/식별자 불일치, 중복 요청, 중단 상태, 공급자 실패를 검사합니다.
DB 상태 흐름과 외부 호출은 모의 처리합니다. 실제 SQL 적용/권한/서버 연결은 위 실행 절차로 확인해야 합니다.

## 실제 ESP32 SW1 / SW2 수신

`migration_real_capture.sql`을 Workbench 관리자 연결에서 한 번 실행합니다.
기존 sensor_sessions, sensor_analysis_runs, sensor_readings는 재사용하고,
sensor_capture_images만 추가합니다. 앱 계정이 hairsense_app@localhost와 다르면
SQL의 GRANT 대상을 실제 앱 계정에 맞춥니다. 이미지 BLOB을 저장하므로
max_allowed_packet은 전송 이미지와 SQL 부가 데이터를 수용해야 합니다.

서버를 실행하는 PowerShell에서 한 기기와 사용자를 지정하고 재시작합니다.

```powershell
$env:HAIRSENSE_REAL_DEVICE_ID = "esp32-device-001"
$env:HAIRSENSE_REAL_USER_ID = "real-user-001"
..\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

위 값은 예시입니다. 테스트하는 실제 기기와 사용자를 계속 같은 ID에 연결하세요.
현재 펌웨어에는 기기/사용자 ID가 없으므로 이 설정은 한 기기·한 사용자 전용입니다.
여러 기기를 연결하거나 사용자를 바꾸기 전에 식별자·인증 전송 규격을 추가해야 합니다.
IP나 boot_id로 기기를 추측하지 않습니다. ID 쿼리 검사는 사용자 인증이 아닙니다.

- SW1: POST /ai/analyze, multipart의 `file`에 JPG, `metadata`에 전자팀 JSON 문자열.
  optical 배열을 읽으며 gyro_x/y/z는 null이어야 합니다.
- SW2: POST /gyro, JSON의 gyro 배열. optical은 null이어야 합니다.
- metadata 없는 기존 이미지 단독 요청도 그대로 동작합니다. 단독 요청은 기존처럼
  로컬 이미지 저장과 모델 분석만 수행하며 새 묶음 DB 저장 대상은 아닙니다.

이미지 모델 파일과 예측 함수는 변경하지 않았습니다. SW1에서는 이미지 바이너리만
기존 모델로 전달하며 광학 값은 별도 센서 처리에 사용합니다.
SW1 이미지 원본은 DB의 image_bytes(LONGBLOB)와 received_images에 저장합니다.
로컬 복사 실패 시에도 이미 커밋한 DB 원본은 남고 local_image_saved=false를 반환합니다.
이미지 분석 결과는 image_result_json에 저장하며 image_status=completed로 확인합니다.
추론 실패는 image_status=failed로 기록하고 원본을 보존합니다.

묶음 원본 JSON은 sensor_sessions.metadata_json의 wire_payload에 보존합니다.
각 샘플은 sensor_readings에 is_synthetic=false로 저장합니다.
전자팀 seq는 묶음 순번이므로 DB 샘플 seq에는 1부터 서버 순번을 부여합니다.
이 순번으로 누락 패킷 수를 계산하면 안 됩니다.
세션 이름은 SW1 real-optical-{묶음 seq}, SW2 real-gyro-{묶음 seq}이며,
device_id + session_id + boot_id 전체로 식별합니다. 같은 부팅 중 seq를 재사용하지 마세요.
동일 묶음 재전송은 cached=true, 같은 키의 다른 내용은 HTTP 409입니다.

센서 처리: 유한 수/시간 증가 검증 → 첫 샘플 기준 2초 창 → 측정 채널별
유효값 수·null 수·평균·표준편차(ddof=0) 계산 → sensor_analysis_runs 저장.
0은 유효하고 null은 채우지 않습니다. SW1 자이로/SW2 광학은 미측정 채널입니다.
20ms 간격에서 벗어난 횟수와 부분 창도 기록합니다. 부분/불규칙 창의 통계는
참고용이며 complete_regular_window=false입니다. 표본이 없는 창은 생성하지 않습니다.
실제 버튼 시작/종료 시각은 알 수 없으므로 내부 끝 시각은 마지막 샘플+20ms로
추정했다는 출처를 저장합니다. envelope timestamp_ms는 원문에 보존합니다.
현재 한 묶음 1~30000샘플, 추정 관측 구간 600초 이하, 이미지 10MiB 이하입니다.

실측 Baseline과 급변 임계값은 아직 없으므로 status=awaiting_real_baseline,
anomaly_score=null, feedback=null입니다. 가상 모델이나 Gemini를 호출하지 않습니다.
실측 평균·표준편차 저장 성공을 개인 이상치 판정 완료로 해석하지 마세요.

전송 후 기존 GET /sensor-sessions/{session_id}/analysis에서
device_id, user_id, boot_id를 넣어 저장된 센서 특징을 조회할 수 있습니다.
문서상 이 GET은 기존 Sensor DB pipeline (synthetic) 그룹에 있지만 실측 저장 결과도 조회합니다.
실측에는 가상 데이터용 complete나 feedback/retry를 호출하지 않습니다.

Workbench 저장 확인:

```sql
USE hairsense_demo;
SELECT device_id,session_id,boot_id,COUNT(*) AS samples
FROM sensor_readings WHERE is_synthetic=0
GROUP BY device_id,session_id,boot_id;

SELECT device_id,session_id,boot_id,image_status,
       OCTET_LENGTH(image_bytes) AS image_size,image_result_json
FROM sensor_capture_images;

SELECT device_id,session_id,boot_id,status,result_json
FROM sensor_analysis_runs WHERE pipeline_version='real_capture_1';
```

수신/DB 트랜잭션/재전송/이미지 실패/HTTP 규격 검사:

```powershell
..\.venv\Scripts\python.exe -m unittest test_real_capture_storage test_sensor_pipeline
```

자동 검사는 DB와 이미지 모델을 모의 처리합니다. 실제 기기 → MySQL → 이미지 모델의
동작 확인은 SQL 적용과 서버 재시작 후 SW1/SW2 전송으로 별도 확인해야 합니다.
