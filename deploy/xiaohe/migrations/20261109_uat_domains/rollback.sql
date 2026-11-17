-- 只回退两个字段；出现会员绑定后拒绝回退，不修改或合并身份。
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '15s';
DO $$
DECLARE changed integer;
BEGIN
  IF current_database() <> 'xiaohe' THEN
    RAISE EXCEPTION 'UAT domain rollback: unexpected database';
  END IF;
  LOCK TABLE xiaohe.users IN SHARE MODE;
  IF EXISTS (SELECT 1 FROM xiaohe.users WHERE system_id='portal') THEN
    RAISE EXCEPTION 'UAT domain rollback: portal member bindings exist';
  END IF;
  UPDATE xiaohe.systems
    SET allowed_origin='https://portal-uat.yuhezhiyuan.com',
        issuer='https://sso-uat.yuhezhiyuan.com'
    WHERE system_id='portal' AND tenant_id=1 AND portal_client_id='app_localportal'
      AND allowed_origin='https://portal-uat.yunhezhiyuan.com'
      AND issuer='https://sso-uat.yunhezhiyuan.com' AND enabled;
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 1 THEN
    RAISE EXCEPTION 'UAT domain rollback: expected exactly one corrected registration';
  END IF;
END $$;
COMMIT;
