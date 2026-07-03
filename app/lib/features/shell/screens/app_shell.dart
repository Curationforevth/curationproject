import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

class AppShell extends StatelessWidget {
  final StatefulNavigationShell navigationShell;
  const AppShell({super.key, required this.navigationShell});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: navigationShell,
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: navigationShell.currentIndex,
        onTap: (index) {
          // 탭바 탭 = 어디서든 해당 탭 루트로 — 열려 있는 바텀시트/푸시 화면을
          // 전부 닫고 이동한다(표준 모바일 컨벤션).
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
