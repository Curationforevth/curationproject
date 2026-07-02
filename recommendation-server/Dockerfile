FROM python:3.11-slim

# glibc malloc arena 단편화 억제 — numpy/대용량 unpickle 시 Linux RSS 가 Mac 대비
# 100MB+ 부풀던 주원인. 무료 512MB 에서 9483 인덱스 unpickle peak 을 낮춰 OOM 방지.
ENV MALLOC_ARENA_MAX=2

# BLAS 스레드 1 고정 — 무료 0.1 vCPU(cgroup 쿼터)에서 OpenBLAS 기본값(호스트
# 코어 수)만큼 스레드가 떠서 쿼터를 나눠 먹으며 경합, 행렬곱이 수십 배 느려지는
# 함정. 쿼터 환경에선 단일 스레드가 최적이고 결과는 동일(스코어 무변경).
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
