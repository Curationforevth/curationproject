# scripts/tests/conftest.py
"""테스트 공통 설정 — scripts/를 sys.path에 추가"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
