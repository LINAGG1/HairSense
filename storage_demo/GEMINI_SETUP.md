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
| provider_timeout / provider_unavailable | 네트워크와 공급자 상태 |
| provider_request_rejected | 모델 사용 가능 여부, 키 제한, 요청 설정; 원문 오류는 노출하지 않음 |
| provider_response_incomplete / output_validation_failed | 응답 잘림, JSON 형식, 고정 관측 문장 변경 등 |

## 문구의 범위

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
