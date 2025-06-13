# 마스터 CLI 명령어

### `start <sha256_hash>`
- 지정된 해시를 찾는 새로운 분산 작업을 시작합니다.

### `stop`
- 현재 실행 중인 작업을 즉시 중단합니다.

### `status`
- 현재 작업 상태, 통계, 활성 워커 정보를 출력합니다.

### `set_rule <id1>:<ratio1> [id2:ratio2] ...`
- 워커별 작업 분배 비율을 동적으로 설정합니다. (`default` 키 사용 가능, 기본은 모든 노드에게 동일하게 배분)
```
Usage: set_rule <worker_id1>:<ratio1> [worker_id2:ratio2 ...]
Example: set_rule worker-{UUID1}:8 worker-{UUID2}:2 
    ㄴ 이럼 노드 1과 노드 2를 8:2로 배분하여 작업을 시킴 CLI 명령어로 작업 중에도 계속 비율을 바꿀 수 있음 
```


### `exit`
- 마스터 서버를 종료합니다.

# 전체 흐름도

1. 마스터 노드 킴

2. 워커 킴(컨테이너 단위든 뭐든)

3. 워커에 마스터 노드를 키고 있는 곳 ip를 등록함. 기본은 로컬 호스트임 
```
WORKER_ID = os.environ.get('WORKER_ID', f'worker-{uuid.uuid4().hex[:6]}')
MASTER_IP = os.environ.get('MASTER_IP', '127.0.0.1')
MASTER_PORT = 5555

```

4. 마스터 명령어로 동적으로 노드 비율을 할당함

---

CandidateGenerator에서 지금 그냥 부르트포스로 후보군을 생성 하고 있으니 여기를 PCFG랑 연동하고 워커도 거기에 맞게 수정하면 될듯 