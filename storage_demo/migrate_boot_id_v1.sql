-- Existing databases only. Back up sensor_readings and stop API/writers first.
-- Run once as an administrator in MySQL Workbench (execute the whole script).
-- MySQL DDL commits implicitly: this migration is NOT transactionally rollbackable.
-- On failure, inspect the table before retrying; restore the backup if necessary.
USE hairsense_demo;

DELIMITER $$
CREATE PROCEDURE migrate_sensor_readings_boot_v1()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sensor_readings'
          AND column_name = 'boot_id'
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'boot_id already exists; inspect migration state before proceeding';
    END IF;
    IF EXISTS (
        SELECT 1 FROM sensor_readings
        WHERE schema_version IS NULL
           OR BINARY schema_version NOT IN ('0.1', '1')
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Unsupported legacy schema_version; migration stopped before changes';
    END IF;

    ALTER TABLE sensor_readings ADD COLUMN boot_id VARCHAR(64) NULL AFTER session_id;

    -- Placeholder means historical boot identity is UNKNOWN, not recovered.
    -- Old PK already enforced unique seq within each device/session.
    -- Version conversion normalizes the storage contract only; timestamps and
    -- sensor values remain untouched and are not certified as v1 captures.
    UPDATE sensor_readings
       SET boot_id = 'legacy-unknown', schema_version = '1';

    ALTER TABLE sensor_readings
        MODIFY COLUMN boot_id VARCHAR(64) NOT NULL,
        MODIFY COLUMN schema_version INT UNSIGNED NOT NULL,
        DROP PRIMARY KEY,
        ADD PRIMARY KEY (device_id, session_id, boot_id, seq);
END$$
DELIMITER ;

CALL migrate_sensor_readings_boot_v1();
DROP PROCEDURE migrate_sensor_readings_boot_v1;

SHOW CREATE TABLE sensor_readings;
