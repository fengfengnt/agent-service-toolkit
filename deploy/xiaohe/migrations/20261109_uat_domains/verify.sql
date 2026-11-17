BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
DO $$
BEGIN
  IF current_database() <> 'xiaohe'
     OR (SELECT count(*) FROM xiaohe.systems
         WHERE system_id='portal' AND tenant_id=1 AND portal_client_id='app_localportal'
           AND allowed_origin='https://portal-uat.yunhezhiyuan.com'
           AND issuer='https://sso-uat.yunhezhiyuan.com' AND enabled) <> 1
     OR EXISTS (SELECT 1 FROM xiaohe.users WHERE system_id='portal'
                AND issuer IS DISTINCT FROM 'https://sso-uat.yunhezhiyuan.com') THEN
    RAISE EXCEPTION 'UAT domain verification: registration or member issuer differs';
  END IF;
END $$;
SELECT system_id, tenant_id, portal_client_id, allowed_origin, issuer, enabled
  FROM xiaohe.systems WHERE system_id='portal';
COMMIT;
