import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

class AppShell extends StatelessWidget {
  final StatefulNavigationShell navigationShell;

  /// 브랜치(탭)별 내비게이터 키. showModalBottomSheet 는 가장 가까운
  /// 내비게이터 = **브랜치** 내비게이터에 route 를 올리므로, 루트만 pop 해서는
  /// 시트가 안 닫힌다(2026-07-03 실기기 회귀). goBranch(initialLocation)도
  /// pageless route(시트/다이얼로그)는 못 걷어낸다 — 키로 직접 pop 이 정본.
  final List<GlobalKey<NavigatorState>> branchNavigatorKeys;

  const AppShell({
    super.key,
    required this.navigationShell,
    required this.branchNavigatorKeys,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: navigationShell,
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: navigationShell.currentIndex,
        onTap: (index) {
          // 탭바 탭 = 어디서든 해당 탭 루트로 — 열려 있는 바텀시트/푸시 화면을
          // 전부 닫고 이동한다(표준 모바일 컨벤션). 어느 브랜치에 남아 있던
          // 시트든 이 시점에 전부 정리한다(복귀 시 재등장 방지).
          for (final key in branchNavigatorKeys) {
            key.currentState?.popUntil((route) => route.isFirst);
          }
          Navigator.of(context, rootNavigator: true)
              .popUntil((route) => route.isFirst);
          if (index == 1) {
            // + tab: push register flow as modal, don't switch tab
            context.push('/register');
          } else {
            navigationShell.goBranch(
              index,
              initialLocation: index == navigationShell.currentIndex,
            );
          }
        },
        items: const [
          BottomNavigationBarItem(
            icon: Icon(Icons.home_outlined),
            activeIcon: Icon(Icons.home),
            label: '홈',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.add),
            label: '등록',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.auto_stories_outlined),
            activeIcon: Icon(Icons.auto_stories),
            label: '서재',
          ),
        ],
      ),
    );
  }
}
