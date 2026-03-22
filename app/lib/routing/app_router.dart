import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../features/auth/providers/auth_provider.dart';
import '../features/auth/screens/login_screen.dart';
import '../features/search/screens/book_search_screen.dart';

GoRouter createRouter(AuthNotifier authNotifier) {
  return GoRouter(
    initialLocation: '/',
    refreshListenable: authNotifier,
    redirect: (context, state) {
      final session = Supabase.instance.client.auth.currentSession;
      final isLoginRoute = state.matchedLocation == '/login';

      if (session == null && !isLoginRoute) return '/login';
      if (session != null && isLoginRoute) return '/';
      return null;
    },
    routes: [
      GoRoute(
        path: '/',
        builder: (context, state) => const HomePage(),
      ),
      GoRoute(
        path: '/login',
        builder: (context, state) => const LoginScreen(),
      ),
      GoRoute(
        path: '/search',
        builder: (context, state) => const BookSearchScreen(),
      ),
    ],
  );
}

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Curation')),
      body: Center(
        child: ElevatedButton(
          onPressed: () => Supabase.instance.client.auth.signOut(),
          child: const Text('로그아웃'),
        ),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => context.push('/search'),
        child: const Icon(Icons.search),
      ),
    );
  }
}
