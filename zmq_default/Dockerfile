# 1. 베이스 이미지 선택
FROM python:3.9-slim

# 2. 시스템 패키지 업데이트 및 해시캣 설치
RUN apt-get update && apt-get install -y hashcat && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 3. 파이썬 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 워커 스크립트 복사
COPY worker.py .

# 5. 컨테이너 시작 시 실행될 명령어
# worker.py 스크립트가 hashcat 프로세스를 직접 제어합니다.
CMD ["python", "-u", "worker.py"]
