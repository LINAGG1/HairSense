
  SELECT
    seq,
    timestamp_ms,
    optical,
    gyro_x,
    gyro_y,
    gyro_z
FROM hairsense_demo.sensor_readings
WHERE device_id = 'mock-device-01'
  AND session_id = 'mock-session-001'
ORDER BY seq
LIMIT 10;