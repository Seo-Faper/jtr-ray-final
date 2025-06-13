# worker.py
import zmq
import hashlib
import time
import uuid
import logging
import os

# --- 워커 설정 ---
# 환경 변수나 인자를 통해 ID를 설정하면 여러 워커를 쉽게 실행 가능
# 예: WORKER_ID=gpu-worker-01 python worker.py
WORKER_ID = os.environ.get('WORKER_ID', f'worker-{uuid.uuid4().hex[:6]}')
MASTER_IP = os.environ.get('MASTER_IP', '127.0.0.1')
MASTER_PORT = 5555

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO, format=f'%(asctime)s - {WORKER_ID} - %(levelname)s - %(message)s')
logger = logging.getLogger('Worker')

class Worker:
    def __init__(self, worker_id, master_ip, master_port):
        self.worker_id = worker_id
        self.master_endpoint = f"tcp://{master_ip}:{master_port}"
        
        # ZMQ 설정 (DEALER 소켓)
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.DEALER)
        # ZMQ ID를 워커 ID로 설정하여 마스터가 식별하기 용이하게 함 (필수 아님)
        self.socket.setsockopt_string(zmq.IDENTITY, self.worker_id)
        
        self.running = False
        self.target_hash = None

    def send_to_master(self, command, payload=None):
        """마스터에게 메시지를 전송합니다."""
        message = [command.encode('utf-8')]
        if payload:
            # payload가 list나 tuple이면 multipart로 전송
            if isinstance(payload, (list, tuple)):
                message.extend(p.encode('utf-8') for p in payload)
            else:
                 message.append(payload.encode('utf-8'))
        self.socket.send_multipart(message)

    def process_candidates(self, candidates_payload):
        """전달받은 후보군을 처리합니다."""
        candidates = candidates_payload.split('\n')
        logger.info(f"Received {len(candidates)} candidates to process.")
        
        for candidate in candidates:
            if not self.running:
                logger.warning("Job stopped while processing, abandoning current batch.")
                break
                
            # 해시 계산 및 비교
            if hashlib.sha256(candidate.encode('utf-8')).hexdigest() == self.target_hash:
                logger.info(f"!!! PASSWORD FOUND: {candidate} !!!")
                self.running = False
                self.send_to_master('FOUND', [candidate, self.worker_id])
                break

    def start(self):
        """워커를 시작하고 마스터에 연결합니다."""
        try:
            self.socket.connect(self.master_endpoint)
            logger.info(f"Connected to master at {self.master_endpoint}")
            
            # 마스터에게 준비되었음을 알림
            self.send_to_master('READY', self.worker_id)

            while True:
                # 마스터로부터 메시지 수신 대기
                command_bytes, *payload_bytes = self.socket.recv_multipart()
                command = command_bytes.decode('utf-8')

                if command == 'control':
                    message = payload_bytes[0].decode('utf-8')
                    if message.startswith('START:'):
                        self.target_hash = message.split(':', 1)[1]
                        self.running = True
                        logger.info(f"Received START command. Target hash: {self.target_hash}")
                    elif message == 'stop':
                        self.running = False
                        logger.info("Received STOP command. Halting operations.")
                
                elif command == 'candidates' and self.running:
                    self.process_candidates(payload_bytes[0].decode('utf-8'))

        except (KeyboardInterrupt, SystemExit):
            logger.info("Worker shutting down.")
        finally:
            self.socket.close()
            self.context.term()

if __name__ == '__main__':
    worker = Worker(WORKER_ID, MASTER_IP, MASTER_PORT)
    worker.start()