# DB에서 센서 분석으로 연결하기

구현 상태 업데이트: sensor_pipeline.py, sensor_pipeline_api.py, migration_sensor_pipeline.sql 및 완료 검증 도구를 작성했습니다.
실제 DB에는 아직 적용하지 않았습니다. 현재 실행 절차와 제한은 DB_PIPELINE_SETUP.md를 따르세요.
아래는 최초 설계 기록입니다. 최초 버전은 세션/boot당 결과 하나를 저장하며 버전별 재분석은 지원하지 않습니다.
현재 GET /sensor-analysis/sessions 및 feedback은 파일 기반 시험 경로입니다.
데이터를 업로드해도 이 경로의 결과가 DB 기반으로 전환되거나 자동 갱신되지는 않습니다.

## 먼저 테스트 데이터를 저장

서버 터미널(storage_demo)에서 기존 서버를 유지합니다. 실행 중이 아니라면:

```powershell
..\.venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

다른 터미널의 storage_demo에서:

```powershell
..\.venv\Scripts\python.exe send_and_verify.py --session-id multi-session-102 --dry-run
..\.venv\Scripts\python.exe send_and_verify.py --session-id multi-session-102
```

manifest에서 test/multi-session-102 폴더를 찾아 사용합니다. 원본이나 라벨은 변경하지 않습니다.
user=mock-multi-user-001, device=mock-multi-device-001, boot=multi-boot-102입니다.
전송 전 60초/20ms/3,000개/유효 30구간을 검사하고, 250개씩 12회 전송합니다.
이후 /readings로 모두 읽어 원본 값과 비교합니다. Gemini는 호출하지 않습니다.
최초 전송 기대값: Inserted=3000, duplicates=0, PASS: all 3000 records match the source.
재전송 기대값: Inserted=0, duplicates=3000. 일부 저장 후 재시도한 경우 두 수의 합이 3000입니다.
동일 식별자로 다른 값이 이미 저장되어 있다면 기존 API는 409를 반환합니다. 덮어쓰거나 삭제하지 않습니다.
옵션 없이 실행하면 기존 mock_data 전송 동작을 유지합니다.

## 확인된 재사용 지점

| 현재 파일/함수 | 연결할 역할 |
| --- | --- |
| app.py: receive(), readings(), database() | 수신·조회·DB 연결 |
| preprocess.py: process(rows, meta) | 전처리 및 품질 검사 |
| analyze.py: extract_features(rows, quality) | 2초별 특징 9개 추출 |
| evaluate_sensor_rules.py: score_rows() | 저장된 IF 및 규칙으로 추론 |
| summarize_sensor_sessions.py: summarize_sensor() | 구간 판정을 세션 요약으로 변환 |
| sensor_feedback.py: generate_feedback() | 요약으로 Gemini 안내 또는 fallback 생성 |

주의: extract_features의 quality.usable과 요약 함수의 플래그는 현재 CSV 문자열 형식을 기대합니다.
서비스 연결 계층에서 자료형을 명시적으로 변환해야 합니다.
기존 스크립트 main()을 호출하면 학습·파일 출력·데이터 재생성이 섞일 수 있으므로 위 함수만 재사용합니다.
모델 번들은 서버에 있는 신뢰된 고정 파일만 로드하며 사용자 경로를 받지 않습니다.
번들의 사용자·기기·단위·주기·규격 및 규칙 일치 여부와 파일 해시를 확인합니다.
테스트 세션으로 재학습하지 않으며 정답 라벨은 추론 입력에 포함하지 않습니다.

## 추가할 구조 (아직 미구현)

1. sensor_pipeline.py: DB에서 받은 rows + 완료 메타데이터를 받아 품질·특징·점수·요약을 만드는 함수.
2. sensor_pipeline_api.py: 완료 요청 및 DB 결과 조회. 라우터 팩토리에 기존 database 함수를 전달해 순환 import를 피합니다.
3. app.py: 라우터 등록, 수신 시 완료된 세션의 신규 데이터 차단 및 사용자 일치 검사.
4. migration_sensor_pipeline.sql: 아래 두 테이블과 필요한 테이블별 권한 추가. 기존 원시 데이터는 보존합니다.
5. 완료/조회 검증 도구 및 테스트: 파일 기준 결과와 일치, 중복 요청, 누락·사용자 불일치·실패 처리 검증.

| 예정 테이블 | 주요 내용 |
| --- | --- |
| sensor_sessions | device/session/boot 복합 키, user, synthetic, 시작 uptime, 측정 길이·주기·단위, 예상 개수, 수신/완료 상태 |
| sensor_analysis_runs | 분석 ID, 세션 참조, 입력 해시, 모델·규칙·코드 버전, 분석 상태, 품질·특징·점수·요약 JSON, 안내 상태 및 Gemini/fallback JSON, 오류 코드, 생성·갱신 시각 |

동일 세션과 분석 버전의 실행에 UNIQUE 제약을 두어 중복 요청은 저장된 결과를 반환합니다.
현재 앱 계정은 원시 데이터 테이블 SELECT/INSERT 권한만 받으므로 신규 테이블의 SELECT/INSERT/UPDATE 권한을 별도로 부여해야 합니다.

예정 API:
- POST /sensor-sessions/{session_id}/complete: user/device/boot와 측정 범위를 명시하고 분석 실행.
- GET /sensor-sessions/{session_id}/analysis: user/device/boot로 저장 결과 조회. GET은 Gemini를 호출하지 않음.

완료 처리와 수신 처리는 동일한 세션 상태 잠금을 사용해야 데이터 추가와 분석이 충돌하지 않습니다.
처음에는 동기 분석으로 구현하되 DB 트랜잭션 안에서 Gemini 응답을 기다리지 않습니다.
분석 결과를 먼저 커밋한 뒤 안내 생성 상태를 원자적으로 확보하고 외부 호출 후 결과를 저장합니다.
Gemini 성공/fallback 모두 저장하여 완료 요청 재전송 시 반복 호출하지 않습니다.
외부 호출 도중 프로세스가 종료되면 호출 성공 여부가 불명확할 수 있으므로 자동 재호출하지 않고 복구 대상으로 표시합니다.

## 첫 구현의 범위

가상 사용자 한 명, 60초, 단일 boot, 완전한 3,000개 측정만 요약·안내 생성합니다.
누락/null이 있으면 품질 결과를 저장하고 데이터 부족으로 응답하며 완전한 세션처럼 안내하지 않습니다.
실측/다중 boot/사용자 인증과 접근 통제는 별도 단계입니다. 지금 API는 로컬 개발 전용입니다.
두피·모발 이미지 모델, get_ai_model(), /ai/analyze는 수정하지 않습니다.
