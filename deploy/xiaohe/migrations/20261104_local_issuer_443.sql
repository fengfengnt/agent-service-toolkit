-- 已批准的一次性本地issuer重命名。须先停止自己的API/worker并备份。
-- 不接受其他库、冲突账号或非预期行数；不改变永久触发器定义。
DO $$
DECLARE changed integer;
BEGIN
  IF current_database() NOT IN ('xiaohe_triad_local_20261104', 'test_xiaohe_agent') THEN
    RAISE EXCEPTION 'issuer cutover precondition: unsupported database' USING ERRCODE='23514';
  END IF;
  PERFORM set_config('lock_timeout', '3s', true);
  LOCK TABLE xiaohe.users, xiaohe.systems IN ACCESS EXCLUSIVE MODE;
  IF (SELECT count(*) FROM xiaohe.systems
      WHERE system_id='portal' AND tenant_id=1 AND issuer='https://sso.lvh.me:8443'
        AND portal_client_id='app_localportal' AND allowed_origin='https://portal.lvh.me:9444') <> 1
     OR (SELECT count(*) FROM xiaohe.users WHERE issuer='https://sso.lvh.me:8443') <> 1
     OR (SELECT count(*) FROM xiaohe.users WHERE issuer='https://sso.lvh.me:8443' AND system_id='portal' AND tenant_id=1 AND source_tenant_id=1) <> 1
     OR EXISTS (SELECT 1 FROM xiaohe.users WHERE issuer='https://sso.lvh.me')
     OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='xiaohe.users'::regclass AND tgname='guard_user_identity' AND tgenabled='O') THEN
    RAISE EXCEPTION 'issuer cutover precondition: registration, count, collision or guard differs' USING ERRCODE='23514';
  END IF;

  -- 独占表锁覆盖临时停用、唯一字段更新和恢复；其他事务不能趁此写用户。
  ALTER TABLE xiaohe.users DISABLE TRIGGER guard_user_identity;
  UPDATE xiaohe.users SET issuer='https://sso.lvh.me'
    WHERE issuer='https://sso.lvh.me:8443' AND system_id='portal' AND tenant_id=1 AND source_tenant_id=1;
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 1 THEN
    RAISE EXCEPTION 'issuer cutover precondition: user row count differs' USING ERRCODE='23514';
  END IF;
  ALTER TABLE xiaohe.users ENABLE TRIGGER guard_user_identity;

  UPDATE xiaohe.systems SET issuer='https://sso.lvh.me'
    WHERE system_id='portal' AND tenant_id=1 AND issuer='https://sso.lvh.me:8443';
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 1 THEN
    RAISE EXCEPTION 'issuer cutover precondition: system row count differs' USING ERRCODE='23514';
  END IF;
END $$;
