import 'package:flutter/material.dart';

/// Curation "Minimal Slate" 컬러 팔레트
/// Slate 900 프라이머리 + 순백 서피스 + 모노크롬 구성
class AppColors {
  // Primary — 버튼, 선택 상태, 네비게이션
  static const primary = Color(0xFF0F172A);       // Slate 900
  static const primaryLight = Color(0xFF334155);  // Slate 700
  static const primaryDark = Color(0xFF020617);   // Slate 950

  // Accent — CTA, 강조 (모노크롬)
  static const accent = Color(0xFF0F172A);        // Slate 900 (primary와 동일)
  static const accentLight = Color(0xFF334155);   // Slate 700
  static const accentDark = Color(0xFF020617);    // Slate 950

  // Surface — 배경 (순백)
  static const surface = Color(0xFFFFFFFF);
  static const surfaceVariant = Color(0xFFF8FAFC);

  // Shelf — 서재 선반
  static const shelf = Color(0xFFE8E4DE);
  static const shelfDark = Color(0xFFDDD8D0);

  // Border
  static const border = Color(0xFFF1F5F9);

  // Shadow
  static const cardShadow = [
    BoxShadow(
      color: Color(0x14000000),
      blurRadius: 8,
      offset: Offset(0, 2),
    ),
    BoxShadow(
      color: Color(0x0A000000),
      blurRadius: 2,
      offset: Offset(0, 1),
    ),
  ];

  // 마일스톤 배경 테마
  static const milestone0 = Color(0xFFF6F1EA);   // 0~9권: 크림/따뜻한 베이지
  static const milestone10 = Color(0xFFE8DFD0);  // 10~29권: 우드톤
  static const milestone30 = Color(0xFF3A3530);   // 30~49권: 짙은 라이브러리 톤
  static const milestone50 = Color(0xFF2A2520);   // 50~99권: 다크 우드
  static const milestone100 = Color(0xFF1A1818);  // 100권+: 풀 다크 라이브러리

  // Text
  static const textPrimary = Color(0xFF0F172A);
  static const textSecondary = Color(0xFF94A3B8);
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
