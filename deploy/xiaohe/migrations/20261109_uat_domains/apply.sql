-- 仅供已备份的 UAT 执行；任何预检失败均中止整个事务。
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '15s';
DO $$
DECLARE changed integer;
BEGIN
  IF current_database() <> 'xiaohe' THEN
    RAISE EXCEPTION 'UAT domain precondition: unexpected database';
  END IF;
  -- 阻止检查之后、提交之前并发创建会员绑定。
  LOCK TABLE xiaohe.users IN SHARE MODE;
  IF EXISTS (SELECT 1 FROM xiaohe.users WHERE system_id='portal') THEN
    RAISE EXCEPTION 'UAT domain precondition: portal member bindings exist';
  END IF;
  UPDATE xiaohe.systems
    SET allowed_origin='https://portal-uat.yunhezhiyuan.com',
        issuer='https://sso-uat.yunhezhiyuan.com'
    WHERE system_id='portal' AND tenant_id=1 AND portal_client_id='app_localportal'
      AND allowed_origin='https://portal-uat.yuhezhiyuan.com'
      AND issuer='https://sso-uat.yuhezhiyuan.com' AND enabled;
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 1 THEN
    RAISE EXCEPTION 'UAT domain precondition: expected exactly one unchanged registration';
  END IF;
END $$;
COMMIT;
