-- migrate_protocol_to_varchar.sql
-- Ubah tbl_instrument.protocol dari ENUM('ASTM','HL7','BCI') → VARCHAR(50)
-- agar protokol baru (mis. COBAS_C111) bisa di-register tanpa schema migration.
--
-- Validasi tetap dilakukan di API level via _PROTOCOL_REGISTRY (lib/protocols).
--
-- Run:
--   mysql -u <user> -p <db> < scripts/migrate_protocol_to_varchar.sql
-- atau via mariadb:
--   mariadb -u <user> -p <db> < scripts/migrate_protocol_to_varchar.sql

ALTER TABLE tbl_instrument
  MODIFY COLUMN protocol VARCHAR(50) NOT NULL;

-- Verifikasi
SHOW COLUMNS FROM tbl_instrument LIKE 'protocol';
