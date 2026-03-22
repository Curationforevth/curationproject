import 'package:flutter/material.dart';

/// Curation "Warm Ink" 컬러 팔레트
/// 딥 블루 + 코랄 악센트 + 크림 서피스
class AppColors {
  // Primary — 버튼, 선택 상태, 네비게이션
  static const primary = Color(0xFF3D5A80);
  static const primaryLight = Color(0xFF5B7BA5);
  static const primaryDark = Color(0xFF2B4060);

  // Accent — CTA, 마일스톤 배지, 강조
  static const accent = Color(0xFFEE6C4D);
  static const accentLight = Color(0xFFF4997F);
  static const accentDark = Color(0xFFD4533A);

  // Surface — 배경 (warm cream)
  static const surface = Color(0xFFF6F1EA);
  static const surfaceVariant = Color(0xFFF0EAE0);

  // Shelf — 서재 선반 (심플 라인)
  static const shelf = Color(0xFFE0D8CC);
  static const shelfDark = Color(0xFFD0C8BC);

  // 마일스톤 배경 테마
  static const milestone0 = Color(0xFFF6F1EA);   // 0~9권: 크림/따뜻한 베이지
  static const milestone10 = Color(0xFFE8DFD0);  // 10~29권: 우드톤
  static const milestone30 = Color(0xFF3A3530);   // 30~49권: 짙은 라이브러리 톤
  static const milestone50 = Color(0xFF2A2520);   // 50~99권: 다크 우드
  static const milestone100 = Color(0xFF1A1818);  // 100권+: 풀 다크 라이브러리

  // Text
  static const textPrimary = Color(0xFF2B2D42);
  static const textSecondary = Color(0xFF8D99AE);
  static const textOnPrimary = Color(0xFFFFFFFF);
  static const textOnAccent = Color(0xFFFFFFFF);

  // Semantic
  static const success = Color(0xFF4CAF50);
  static const error = Color(0xFFD32F2F);
  static const warning = Color(0xFFFFA726);

  // 책등 색상 — 제목 해시 기반 팔레트
  static const spineColors = [
    Color(0xFF3D5A80), // 딥 블루
    Color(0xFFEE6C4D), // 코랄
    Color(0xFF5B7B6F), // 뮤트 그린
    Color(0xFFC67B5C), // 테라코타
    Color(0xFF8B6F8E), // 뮤트 퍼플
    Color(0xFF4A7C6F), // 틸
    Color(0xFFB5838D), // 로즈
    Color(0xFF6B8F71), // 세이지
    Color(0xFFD4A574), // 웜 탄
    Color(0xFF7B8FA1), // 스틸 블루
  ];

  /// 제목 해시 기반 책등 색상
  static Color spineColorFromTitle(String title) {
    final hash = title.codeUnits.fold(0, (sum, c) => sum + c);
    return spineColors[hash % spineColors.length];
  }
}
