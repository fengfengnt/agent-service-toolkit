-- 已授权的本地Portal Origin迁移；调用方须停自己的API/worker并使用事务。
DO $$
DECLARE changed integer;
BEGIN
  IF current_database() NOT IN ('xiaohe_triad_local_20261104', 'test_xiaohe_agent') THEN
    RAISE EXCEPTION 'portal cutover precondition: unsupported database' USING ERRCODE='23514';
  END IF;
  PERFORM set_config('lock_timeout', '3s', true);
  LOCK TABLE xiaohe.systems IN ACCESS EXCLUSIVE MODE;
  IF (SELECT count(*) FROM xiaohe.systems
      WHERE system_id='portal' AND tenant_id=1 AND issuer='https://sso.lvh.me'
        AND portal_client_id='app_localportal' AND allowed_origin='https://portal.lvh.me:9444'
        AND enabled) <> 1 THEN
    RAISE EXCEPTION 'portal cutover precondition: registration differs' USING ERRCODE='23514';
  END IF;
  UPDATE xiaohe.systems SET allowed_origin='https://portal.lvh.me'
    WHERE system_id='portal' AND tenant_id=1 AND allowed_origin='https://portal.lvh.me:9444';
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 1 THEN
    RAISE EXCEPTION 'portal cutover precondition: row count differs' USING ERRCODE='23514';
  END IF;
END $$;
