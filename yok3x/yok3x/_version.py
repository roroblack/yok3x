"""버전 단일 출처.

setuptools(pyproject dynamic)가 패키지를 임포트하지 않고 이 파일만 정적 파싱해
버전을 읽는다 — 런처 yok3x.py와 패키지 yok3x/의 이름 충돌을 피하기 위함.
런타임에는 __init__ 이 이 값을 재노출한다.
"""
__version__ = "3.1.0"
