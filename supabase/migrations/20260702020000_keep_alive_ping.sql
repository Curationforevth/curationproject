-- keep-alive ping — pg_cron + pg_net 으로 Render 무료 인스턴스 sleep 방지
--
-- 배경: .github/workflows/keep-alive.yml 의 10분 크론이 GitHub Actions 스케줄
-- 스로틀 때문에 실제로는 하루 ~8회만 실행됨(최근 5일 실측) → 서버가 대부분
-- 잠들어 있어 앱 진입이 거의 항상 cold wake(20~30s)를 맞음. 홈 큐레이션이
-- "안 뜨는" 근본 원인. pg_cron 은 DB 내부 스케줄러라 스로틀 없이 정시 실행.
--
-- 윈도우는 GitHub 워크플로와 동일: KST 06:00~02:00(활성 시간대)만 깨워둠.
-- Render 무료 750 instance-hours/월 한도 대비 ~600h 로 안전 마진 유지.
-- (KST 02~06 = UTC 17~20 시는 핑 제외 → sleep 허용)
--
-- net.http_get 은 비동기(요청 큐잉 후 즉시 반환)라 DB 부하 없음. 응답은
-- net._http_response 에 쌓였다가 pg_net TTL(기본 6h)로 자동 정리됨.
-- Render 는 edge 가 요청을 받는 순간 wake 를 시작하므로, 응답 타임아웃이
-- wake 완료보다 짧아도 깨우는 효과는 유효하다. 여유 있게 60s 로 설정.
--
-- 기존 GitHub keep-alive.yml 은 유지(드물게라도 돌 때 /health 5xx 를 빨간불로
-- 보여주는 무료 uptime 모니터 역할).

CREATE EXTENSION IF NOT EXISTS pg_net;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='keep-alive-render') THEN
    PERFORM cron.unschedule('keep-alive-render');
  END IF;
  PERFORM cron.schedule('keep-alive-render', '*/10 0-16,21-23 * * *',
    $ping$
    SELECT net.http_get(
      url := 'https://curation-recommendation.onrender.com/health',
      timeout_milliseconds := 60000
    )
    $ping$);
END $$;
