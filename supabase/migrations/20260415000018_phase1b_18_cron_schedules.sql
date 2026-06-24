-- Phase 1B — 18_cron_schedules
-- idempotent wrap: 이미 등록된 job은 unschedule 후 재등록

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='refresh-curation-cache') THEN
    PERFORM cron.unschedule('refresh-curation-cache');
  END IF;
  PERFORM cron.schedule('refresh-curation-cache', '5 * * * *',
    'SELECT refresh_curation_cache_all()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='aggregate-co-occurrence') THEN
    PERFORM cron.unschedule('aggregate-co-occurrence');
  END IF;
  PERFORM cron.schedule('aggregate-co-occurrence', '0 17 * * *',
    'SELECT aggregate_co_occurrence()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='refresh-user-top-taste') THEN
    PERFORM cron.unschedule('refresh-user-top-taste');
  END IF;
  PERFORM cron.schedule('refresh-user-top-taste', '15 17 * * *',
    'SELECT refresh_user_top_taste_all()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='refresh-fallback-curation') THEN
    PERFORM cron.unschedule('refresh-fallback-curation');
  END IF;
  PERFORM cron.schedule('refresh-fallback-curation', '30 17 * * *',
    'SELECT refresh_fallback_curation()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='deactivate-curations') THEN
    PERFORM cron.unschedule('deactivate-curations');
  END IF;
  PERFORM cron.schedule('deactivate-curations', '45 17 * * *',
    'SELECT deactivate_curations()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='check-stage-transition') THEN
    PERFORM cron.unschedule('check-stage-transition');
  END IF;
  PERFORM cron.schedule('check-stage-transition', '0 18 * * *',
    'SELECT check_stage_transition()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='cleanup-user-curation-history') THEN
    PERFORM cron.unschedule('cleanup-user-curation-history');
  END IF;
  PERFORM cron.schedule('cleanup-user-curation-history', '0 20 1 * *',
    'SELECT cleanup_user_curation_history()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='cleanup-home-section-cache') THEN
    PERFORM cron.unschedule('cleanup-home-section-cache');
  END IF;
  PERFORM cron.schedule('cleanup-home-section-cache', '0 20 15 * *',
    'SELECT cleanup_home_section_cache()');
END $$;
