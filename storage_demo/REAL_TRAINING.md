# 실제 센서 학습

`storage_demo` 폴더에서 실행합니다. 옵션 없이 실행하면 기존 가상 실험을 유지합니다.

```powershell
python analyze_sessions.py --real-dataset ../real_sessions/gyro_001 --output ../analysis_results/real/gyro_001
```

출력 폴더는 아직 존재하지 않아야 합니다. DB는 자동 조회하거나 수정하지 않습니다.
DB에서 내보낸 센서 원본을 세션 폴더마다 `sensor_readings.jsonl`로 저장하고,
`sensor_sessions.metadata_json`을 JSON 객체 형태의 `metadata.json`으로 저장하세요.
행에는 `schema_version`, `boot_id`, `seq`, `timestamp_ms`와 해당 센서 필드가 필요합니다.
행에 포함된 사용자·장치·세션 ID와 `is_synthetic`은 메타데이터와 일치해야 합니다.

데이터셋 루트의 `manifest.json` 예시:

```json
{
  "is_synthetic": false,
  "sensor": "gyro",
  "sessions": [
    {"directory": "session_01", "session_id": "real-gyro-1", "boot_id": "boot-a", "split": "train", "session_order": 1, "baseline_normal": true},
    {"directory": "session_02", "session_id": "real-gyro-2", "boot_id": "boot-a", "split": "calibration", "session_order": 2, "baseline_normal": true},
    {"directory": "session_03", "session_id": "real-gyro-3", "boot_id": "boot-a", "split": "test", "session_order": 3}
  ]
}
```

- 광학은 `sensor: "optical"`로 별도 실행합니다. 한 실행은 한 사용자·장치 기준입니다.
- 메타데이터에는 ID들, `is_synthetic: false`, `schema_version: 1`, `sample_rate_hz: 50`,
  `start_timestamp_ms`, `end_timestamp_ms`, `sample_type`(`gyro` 또는 `camera_optical`),
  해당 단위(`gyro_unit: "deg/s"` 또는 `optical_unit: "V"`)가 필요합니다.
- 정상으로 확인한 세션만 train/calibration에 넣으세요. `baseline_normal`은 확인 사실의 기록이며 자동 판정이 아닙니다.
- `session_order`는 실제 수집 순서입니다. train → calibration → test 순서로 나눕니다.
  각 split에 최소 한 세션, train에는 최소 두 개의 사용 가능한 2초 구간이 필요합니다.
  이는 실행 최소 조건이며 충분한 데이터 양을 뜻하지 않습니다.
- 2초마다 100개 샘플, 각 20ms 슬롯에서 최대 5ms 편차를 허용합니다.
  누락·비정상 값·부분 구간은 제외하여 `quality_reports.json`에 기록합니다.
  보간하지 않으며 품질 기준은 실제 장치 특성에 맞게 검증해야 합니다.
- 평균·표준편차로 Isolation Forest를 학습하며 가상 급변 임계값을 사용하지 않습니다.
- 정답 라벨 없이 실행할 수 있습니다. `scores.csv`에 이상 점수를 저장하며 정확도·재현율은 계산하지 않습니다.
- 모델, 특징, 품질 보고서, 임계값, 실행 요약을 저장합니다. 기존 가상 모델용 추론 API에는 자동 연결하지 않습니다.
