# 전자팀 OPT101 전송 규격 (카메라 불필요)

`POST /ai/analyze`와 `POST /optical` 모두 `application/json`을 받습니다.
이미지나 multipart의 `file` 필드는 필요하지 않습니다.

```python
data = {
    "schema_version": 1,
    "sample_type": "optical",
    "boot_id": "3AC6BDB3",
    "seq": 2,
    "timestamp_ms": 1040,
    "optical": [
        {"timestamp_ms": 1000, "value": 0.12},
        {"timestamp_ms": 1020, "value": 0.13}
    ]
}
response = requests.post(
    "http://172.20.119.24:8000/ai/analyze", json=data, timeout=15
)
print(response.status_code, response.text)
response.raise_for_status()
```

위 값은 형식 예시입니다. 실제로는 측정한 샘플 전체를 optical 배열에 넣으세요.
`value`는 V 단위이며 ADC 원시 정수를 전압으로 자동 변환하지 않습니다.
sample_type은 `optical`을 권장하고, 기존 `camera_optical`도 허용합니다.
sample_type 생략 시 optical, 미측정 gyro_x/y/z 생략 시 null입니다.
광학 요청의 `gyro` 필드는 생략하거나 `null`로 보낼 수 있습니다. 실제 자이로 배열은 `/gyro`로 보냅니다.
schema_version, boot_id, seq, timestamp_ms, optical은 필수입니다.
한 요청은 1~30000개 샘플, 최대 8MiB JSON, 관측 구간 600초 이내입니다.

JSON 성공 응답은 저장 결과 객체입니다: status=stored, reading_count=샘플 수,
image_status=not_present, is_synthetic=false, sensor_analysis_status=awaiting_real_baseline.
센서 원본·세션·기술통계만 저장하고 이미지 모델은 호출하지 않습니다.
동일 묶음 재전송은 cached=true, 같은 키의 다른 내용은 409입니다.
새 테이블 생성은 필요하지 않지만 sensor_readings, sensor_sessions,
sensor_analysis_runs 테이블과 기존 장치/사용자 환경변수 설정이 필요합니다.
아직 실제 학습 모델을 온라인 추론에 연결한 것은 아닙니다.

## 기존 이미지 업로드 호환 경로

`POST /optical` 또는 `POST /ai/analyze`로 전송합니다.
Content-Type은 `multipart/form-data; boundary=...`입니다.

| multipart 필드 | 내용 |
|---|---|
| `file` | filename이 있는 이미지 바이너리(JPEG/PNG/WEBP), 최대 10MiB |
| `metadata` | 아래 형식의 JSON **문자열**. 파일 첨부가 아닌 텍스트 필드 |

```json
{
  "schema_version": 1,
  "sample_type": "camera_optical",
  "boot_id": "3AC6BDB3",
  "seq": 2,
  "timestamp_ms": 1040,
  "optical": [
    {"timestamp_ms": 1000, "value": 0.12},
    {"timestamp_ms": 1020, "value": 0.13}
  ],
  "gyro_x": null,
  "gyro_y": null,
  "gyro_z": null
}
```

값은 형식 설명용입니다. 실제 전송은 실제 측정값을 사용합니다.
전압은 V, 시간은 ms입니다. 배열 내 시간은 엄격히 증가해야 합니다.
boot_id는 부팅마다 변경하고, 같은 부팅에서 측정 묶음 seq를 재사용하지 않습니다.
`schema_version`은 문자열이 아닌 정수 1입니다.

이미지를 함께 보낼 때만 아래 multipart 규격을 사용합니다. JPEG 바이너리만 보내는 요청은 지원하지 않습니다.
이미지 필드 이름은 `image`가 아닌 `file`입니다. multipart boundary는 헤더와 본문이 같아야 합니다.
이미지와 센서 JSON을 별도 요청으로 보내는 방식은 현재 지원하지 않습니다.

실제 metadata를 `capture.json`, 실제 이미지를 `capture.jpg`로 저장한 경우 PowerShell 예시:

```powershell
curl.exe -i "http://서버주소:8000/optical" -F "file=@capture.jpg;type=image/jpeg" -F "metadata=<capture.json"
```

curl이 multipart 헤더와 boundary를 만들므로 Content-Type 헤더를 따로 지정하지 않습니다.
`/optical`은 metadata가 필수입니다. `/ai/analyze`는 기존 이미지 단독 요청도 지원하므로
센서 수집 시 반드시 metadata를 포함하세요.

422 응답의 `detail` 및 서버의 `Request validation failed` / `Optical metadata validation failed` 로그를 확인하세요.
`body.file`은 파일 필드 누락, `body.metadata`는 metadata 누락,
`invalid_optical_metadata`의 errors는 JSON 내부의 잘못된 필드를 뜻합니다.
JSON 형식 오류도 `invalid_optical_metadata`의 errors에 표시됩니다.
503 `real_device_and_user_binding_required`가 나오면 서버 실행 환경에
HAIRSENSE_REAL_DEVICE_ID와 HAIRSENSE_REAL_USER_ID를 설정해야 합니다.
기존 서버를 Ctrl+C로 종료하고 storage_demo 폴더에서 `./start_server.ps1`을 실행하면
DeviceId와 UserId를 입력받아 같은 프로세스 환경에 설정한 뒤 서버를 시작합니다.
또는 `./start_server.ps1 -DeviceId 실제장치ID -UserId 실제사용자ID`로 실행합니다.
ID에는 영문·숫자·밑줄·하이픈만 사용하며 길이는 1~64자입니다.
기존 데이터를 계속 수집할 때는 이전과 같은 ID를 입력하세요.
이 설정은 한 기기·한 사용자 전용입니다.

변경 적용에는 uvicorn 재시작이 필요합니다. 자동 테스트는 DB·모델을 모의 처리하므로
실제 장치 → DB 저장 성공은 실제 전송 후 별도로 확인해야 합니다.
