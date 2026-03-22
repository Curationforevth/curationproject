/// 앱 전체 상수값
class AppConstants {
  /// 서가 뷰: 한 선반당 최대 책 수
  static const maxBooksPerShelf = 7;

  /// 커버 피드: 작가 그룹핑 최소 권수
  static const minBooksForAuthorGroup = 2;

  /// 커버 피드: 무드 태그 그룹핑 최소 권수
  static const minBooksForMoodGroup = 5;

  /// 마일스톤 경계값
  static const milestoneThresholds = [0, 10, 30, 50, 100];

  /// 마일스톤 레벨 계산
  static int milestoneLevel(int bookCount) {
    if (bookCount >= 100) return 4;
    if (bookCount >= 50) return 3;
    if (bookCount >= 30) return 2;
    if (bookCount >= 10) return 1;
    return 0;
  }
}
