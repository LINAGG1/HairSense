# 센서 안내 문구 연결

이 기능은 로컬 가상 데이터용입니다. 이미지 모델, 센서 점수, 판정 기준을 변경하지 않습니다.
기존 GET 조회는 외부 API를 호출하지 않습니다.

## 서버 터미널에서 설정

실행 중인 서버를 Ctrl+C로 종료한 다음, `storage_demo` 폴더의 PowerShell에서 실행합니다.

```powershell
$env:GEMINI_API_KEY = ([System.Net.NetworkCredential]::new('', (Read-Host 'Gemini API key' -AsSecureString))).Password
$env:GEMINI_MODEL = 'gemini-3.5-flash-lite'
..\.venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

입력 프롬프트에 키를 붙여넣고 Enter를 누릅니다. 키를 채팅, Python 코드, Git에 넣지 마세요.
이 설정은 현재 터미널과 그 터미널에서 실행한 서버에 적용됩니다. 새 터미널에서는 다시 설정합니다.
이 구현은 `.env` 파일을 자동으로 읽지 않습니다. 기존 requests 패키지를 사용하므로 추가 SDK 설치는 필요하지 않습니다.

## 호출

브라우저에서 http://127.0.0.1:8000/docs 를 엽니다.

1. `POST /sensor-analysis/sessions/{session_id}/feedback`을 선택합니다.
2. **Try it out**을 누릅니다.
3. `session_id`에 `multi-session-102`를 입력합니다.
4. **Execute**를 누릅니다. 별도 요청 본문은 없습니다.

Execute마다 Gemini 요청을 한 번 보냅니다. 자동 재시도와 캐시는 없으며, 공급자 요금/할당량이 적용됩니다.
사용자/기기/세션 식별자, 원시 센서 데이터, 카메라 이미지는 전송하지 않습니다.
전송하는 정보는 가상 데이터 표시, 센서별 구간 수·시간·비율·규칙 관측 결과입니다.

`feedback.source`가 `gemini`이면 외부 응답의 형식과 문구 검사를 통과한 결과입니다.
`fallback`이면 서버의 기본 문구이며, Gemini 성공으로 해석하면 안 됩니다.

| fallback_reason | 확인할 내용 |
| --- | --- |
| missing_api_key | 서버를 실행한 터미널의 환경변수 |
| invalid_model_configuration | GEMINI_MODEL의 모델 이름 |
| provider_auth_error | 키와 모델 접근 권한 |
| provider_rate_limited | 공급자 할당량/호출 제한 |
| provider_model_unavailable | 현재 모델을 이 키와 API 버전에서 찾거나 사용할 수 있는지 확인 |
| provider_invalid_request | 요청 필드·모델 기능·프로젝트 사용 조건 확인 |
| provider_timeout / provider_unavailable | 네트워크와 공급자 상태 |
| provider_request_rejected | 모델 사용 가능 여부, 키 제한, 요청 설정; 원문 오류는 노출하지 않음 |
| provider_response_incomplete / output_validation_failed | 응답 잘림, JSON 형식, 고정 관측 문장 변경 등 |

오류를 확인할 때는 실제 Server response의 `feedback`에서 다음 네 항목을 확인합니다.
`fallback_reason`, `provider_http_status`, `provider_error_status`, `provider_error_reason`.
HTTP 200은 로컬 API가 기본 문구를 반환한 경우에도 표시됩니다.
Swagger의 Example Value는 실제 호출 결과가 아닙니다.
Google 오류 원문/키/프로젝트 메타데이터는 반환하지 않고, 알려진 오류 코드만 반환합니다.

## 상세 오류 코드가 없는 HTTP 400 진단

서버를 Ctrl+C로 멈추고, 키를 설정했던 동일한 PowerShell의 `storage_demo`에서 실행합니다.

```powershell
..\.venv\Scripts\python.exe diagnose_gemini.py
```

이 명령은 현재 모델과 실제 센서 안내 요청 형식으로 외부 요청을 딱 한 번 보냅니다.
자동 재시도는 없으며 공급자 요금/할당량이 적용될 수 있습니다.
오류 메시지에서 설정한 키, 일반적인 Google 키 형태, URL, 이메일, projects/ 식별자를 가립니다.
비 JSON 응답은 본문을 출력하지 않습니다. 출력 공유 전 다른 식별 정보도 없는지 확인하세요.
HTTP 200만으로 안내 문구 검증까지 성공한 것은 아니므로, 이후 서버를 다시 실행해 Swagger에서 확인합니다.

HTML 오류가 반환되는 경우, 같은 키와 모델의 정보 조회를 별도로 검사할 수 있습니다.

```powershell
..\.venv\Scripts\python.exe diagnose_gemini.py --check-model
```

이 옵션은 `models.get` 조회 한 번만 수행하며 센서 파일을 읽거나 생성 요청을 보내지 않습니다.
조회 성공은 문장 생성 성공을 보장하지 않습니다. 결과를 보고 생성 요청과 접근 문제를 구분합니다.

## 문구의 범위

### 저장된 DB 요약으로 출력 검증 오류 진단

서버를 실행한 채, 별도 storage_demo 터미널에도 GEMINI_API_KEY를 설정하고 실행합니다.

```powershell
..\.venv\Scripts\python.exe diagnose_gemini.py --check-feedback
```

DB 결과 조회 GET으로 multi-session-102의 저장된 요약을 가져와 새로운 Gemini 요청을 한 번 보냅니다.
DB 결과/캐시를 변경하지 않습니다. API 요금/할당량이 적용될 수 있습니다.
생성은 매번 다를 수 있으므로 과거 실패 응답을 복원하는 검사가 아닙니다.
검증 기준/프롬프트는 변경하지 않았으며 원문을 출력하지 않고 고정 오류 코드와 센서 이름만 보여줍니다.

| validation_error_code | 의미 |
| --- | --- |
| message_schema_mismatch | 필수 필드·자료형·문장 길이 등 불일치 |
| finding_changed | 관측 분류 변경 |
| fixed_fact_changed | 고정 관측 문장 변경 |
| unsupported_advice_content | 추가 안내에서 금지한 수치·표현 감지 |
| advice_language_or_ending | 한국어/허용 문장 종결 형식 검사 실패 |
| generated_json_invalid | 생성 문장이 유효한 JSON이 아님 |
| generated_text_too_long | 생성 텍스트가 처리 길이 제한 초과 |
| provider_response_not_json / provider_response_structure | 공급자 응답 파싱·구조 검사 실패 |

이전에 저장된 fallback에는 새 오류 코드가 없고 그대로 유지됩니다.
`unsupported_advice_content`일 때 `validation_error_rules`에 걸린 규칙을 함께 표시합니다.
예: `term:측정`, `number_or_markup`, `url`. 원문이나 실제 숫자·URL을 출력하지 않습니다.
이는 문자열 규칙의 일치 결과이며 실제로 잘못된 의미의 문장이라는 확정 판정은 아닙니다.
`sensor_feedback_2`에서는 추가 안내 문장 앞의 `측정할 때는`, `측정 시에는` 등 사용 상황 표현을 허용합니다.
측정이라는 단어를 전면 허용하는 것은 아니며 뒤에 붙는 수치·진단·관측 방향 제한은 유지합니다.
예전 실패 응답 원문은 저장하지 않았으므로 그 문장 자체가 무해했는지는 소급 확인할 수 없습니다.

Python이 확정한 관측 문장을 첫 문장으로 고정하고, Gemini가 사용 안내를 덧붙입니다.
모델이 새로운 숫자나 관측 방향을 만들지 않도록 JSON/문구를 검사합니다.
추가 문장 검사는 보수적인 문자열 검사이며, 자유 문장의 의미를 완전히 보증하지 않습니다.
사용자 대상 배포 전 문구 검토와 실측 검증이 필요합니다.
현재 결과는 세션 요약이며 하루 전체 평가, 질병 판단, 건강 위험 확률이 아닙니다.

## 개발 검증

```powershell
..\.venv\Scripts\python.exe -m unittest test_sensor_analysis_api test_sensor_feedback -v
```

테스트는 외부 Gemini 요청을 모의 처리합니다. 실제 API 연결은 키 설정 후 Swagger로 확인해야 합니다.

공식 문서 확인일: 2026-09-23.
- [Gemini 3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)
- [GenerateContent](https://ai.google.dev/api/generate-content)
- [구조화 출력](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)
