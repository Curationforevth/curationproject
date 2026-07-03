"""v3_reason_extract 최종 exit code 판정 테스트

부분 에러(임계치 이하)는 exit 0 + 요약 로그, 임계치 초과·중단·전량 실패는
exit 1 (KI-002 fail-loud 유지). 분모는 QC(D2/I2)와 동일하게 no_data 제외.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from v3_reason_extract import resolve_exit_code, EXIT_MAX_ERROR_RATIO


class TestResolveExitCode:
    def test_no_errors_exits_zero(self):
        code, msg = resolve_exit_code(total_done=200, total_skipped_no_data=0,
                                      total_errors=0, aborted=False)
        assert code == 0

    def test_errors_below_threshold_exit_zero(self):
        # 실측 사례: 200권 처리, 에러 6건 (3%) → 성공 처리
        code, msg = resolve_exit_code(total_done=200, total_skipped_no_data=0,
                                      total_errors=6, aborted=False)
        assert code == 0
        # 에러를 숨기지 않는다 — 요약 메시지에 건수·비율 명시
        assert "6" in msg
        assert "%" in msg

    def test_errors_at_threshold_exit_zero(self):
        # "초과 시에만" exit 1 — 정확히 5% 는 통과
        code, _ = resolve_exit_code(total_done=200, total_skipped_no_data=0,
                                    total_errors=10, aborted=False)
        assert code == 0

    def test_errors_above_threshold_exit_one(self):
        code, msg = resolve_exit_code(total_done=200, total_skipped_no_data=0,
                                      total_errors=20, aborted=False)
        assert code == 1
        assert "%" in msg

    def test_no_data_excluded_from_denominator(self):
        # 200권 중 100권이 데이터부족 스킵이면 분모는 100 → 6건 = 6% > 5%
        code, _ = resolve_exit_code(total_done=200, total_skipped_no_data=100,
                                    total_errors=6, aborted=False)
        assert code == 1

    def test_zero_done_with_errors_exit_one(self):
        # 상세조회 전멸 등 처리 0권 + 에러만 있으면 실패
        code, _ = resolve_exit_code(total_done=0, total_skipped_no_data=0,
                                    total_errors=20, aborted=False)
        assert code == 1

    def test_aborted_always_exit_one(self):
        # 연속 에러/QC 실패로 중단된 run 은 에러율 낮아도 실패
        code, msg = resolve_exit_code(total_done=200, total_skipped_no_data=0,
                                      total_errors=3, aborted=True)
        assert code == 1
        assert "중단" in msg

    def test_threshold_constant_is_five_percent(self):
        assert EXIT_MAX_ERROR_RATIO == 0.05
