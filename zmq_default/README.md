# 실시간 스트리밍 분산 암호 크래커 (Hashcat 연동 버전)

ZeroMQ를 사용하여 마스터 노드가 실시간으로 생성하는 암호 후보군을 여러 워커 노드로 스트리밍하고, 각 워커는 Hashcat을 이용해 크래킹을 수행하는 고성능 분산 시스템입니다.

## 아키텍처

- **master.py**: 암호 후보군을 생성하여 `tcp://*:5555` 포트로 방송합니다.
- **worker.py**: `master.py`의 방송을 구독하여 받은 내용을 표준 출력(stdout)으로 출력합니다.
- **Dockerfile**: `worker.py`의 출력을 `hashcat --stdin`의 입력으로 파이프 연결하여 컨테이너를 실행합니다.

## 실행 방법

### 1단계: 마스터 노드 실행 (마스터 PC)

```bash
# 필요한 라이브러리 설치
pip install -r requirements.txt

# 마스터 스크립트 실행
python master.py


docker build -t cracking-worker:latest .
docker run --rm \
  -e MASTER_IP='210.97.86.26' \
  -e DASHBOARD_IP='210.97.86.26' \
  -e TARGET_HASH='888DF25AE35772424A560C7152A1DE794440E0EA5CFEE62828333A456A506E05' \
  cracking-worker:latest

```