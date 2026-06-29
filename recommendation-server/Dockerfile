FROM python:3.11-slim

# glibc malloc arena 단편화 억제 — numpy/대용량 unpickle 시 Linux RSS 가 Mac 대비
# 100MB+ 부풀던 주원인. 무료 512MB 에서 9483 인덱스 unpickle peak 을 낮춰 OOM 방지.
ENV MALLOC_ARENA_MAX=2

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
