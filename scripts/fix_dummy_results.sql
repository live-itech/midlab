-- scripts/fix_dummy_results.sql
-- Patch 2 record dummy (id 1 & 2) yang failed karena result_json kosong.
-- Isi data dummy valid sesuai kontrak EazyApp Instrument API supaya
-- POST /results balas 2xx → bridge update send_status='sent'.
--
-- Jalankan:
--   sudo mysql midlab_db < scripts/fix_dummy_results.sql
--
-- Catatan: build_mid_payload tetap menormalkan (status F→final,
-- protocol COBAS_C111→ASTM, instrument_id→string LIS, comments di-drop).
-- message_datetime sudah ISO8601 jadi di-passthrough.

UPDATE tbl_result
SET
  result_json = JSON_OBJECT(
    'mid_version', '1.0',
    'instrument_id', 1,
    'protocol', 'COBAS_C111',
    'message_id', CONCAT('MSG-DUMMY-', id),
    'message_datetime', '2026-05-15T10:00:00+07:00',
    'patient', JSON_OBJECT(
      'patient_id', 'RM-DUMMY-0001',
      'name', 'Pasien Dummy Satu',
      'dob', '1990-01-01',
      'gender', 'M',
      'physician', 'dr. Dummy'
    ),
    'specimen', JSON_OBJECT(
      'sample_id', 'BC-DUMMY-0001',
      'sample_type', 'Serum',
      'collected_at', '2026-05-15T09:30:00+07:00'
    ),
    'order', JSON_OBJECT(
      'order_id', 'LAB-DUMMY-0001',
      'panel', 'Kimia Klinik'
    ),
    'results', JSON_ARRAY(
      JSON_OBJECT('test_code','GLU','test_name','Glukosa Sewaktu',
                  'value','95','unit','mg/dL','reference_range','70-115',
                  'flag','N','status','F'),
      JSON_OBJECT('test_code','CREA','test_name','Kreatinin',
                  'value','0.9','unit','mg/dL','reference_range','0.6-1.2',
                  'flag','N','status','F')
    ),
    'comments', JSON_ARRAY(),
    'parse_errors', JSON_ARRAY()
  ),
  send_status = 'pending',
  retry_count = 0,
  error_message = NULL
WHERE id = 1 AND send_status = 'failed';

UPDATE tbl_result
SET
  result_json = JSON_OBJECT(
    'mid_version', '1.0',
    'instrument_id', 1,
    'protocol', 'COBAS_C111',
    'message_id', CONCAT('MSG-DUMMY-', id),
    'message_datetime', '2026-05-15T10:05:00+07:00',
    'patient', JSON_OBJECT(
      'patient_id', 'RM-DUMMY-0002',
      'name', 'Pasien Dummy Dua',
      'dob', '1985-06-15',
      'gender', 'F',
      'physician', 'dr. Dummy'
    ),
    'specimen', JSON_OBJECT(
      'sample_id', 'BC-DUMMY-0002',
      'sample_type', 'Serum',
      'collected_at', '2026-05-15T09:35:00+07:00'
    ),
    'order', JSON_OBJECT(
      'order_id', 'LAB-DUMMY-0002',
      'panel', 'Kimia Klinik'
    ),
    'results', JSON_ARRAY(
      JSON_OBJECT('test_code','CHOL','test_name','Kolesterol Total',
                  'value','210','unit','mg/dL','reference_range','<200',
                  'flag','H','status','F'),
      JSON_OBJECT('test_code','UA','test_name','Asam Urat',
                  'value','5.4','unit','mg/dL','reference_range','3.5-7.2',
                  'flag','N','status','F')
    ),
    'comments', JSON_ARRAY(),
    'parse_errors', JSON_ARRAY()
  ),
  send_status = 'pending',
  retry_count = 0,
  error_message = NULL
WHERE id = 2 AND send_status = 'failed';

-- Verifikasi
SELECT id, send_status, retry_count,
       JSON_EXTRACT(result_json, '$.order.order_id') AS order_id,
       JSON_LENGTH(result_json, '$.results') AS n_results
FROM tbl_result WHERE id IN (1, 2);
