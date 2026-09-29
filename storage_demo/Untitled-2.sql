SELECT COUNT(*) AS legacy_count
FROM sensor_readings_legacy_01;

SELECT COUNT(*) AS new_count
FROM sensor_readings;

SHOW CREATE TABLE sensor_readings;