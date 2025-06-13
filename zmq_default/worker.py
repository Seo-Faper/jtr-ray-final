import zmq
import sys
import logging
import uuid
import subprocess
import os
import tempfile
import time
import requests
from threading import Thread
from subprocess import PIPE
class WorkerNode:
    """
    마스터와 동기화 후, 작업을 수신하고 자신의 상태를 주기적으로 보고하는 워커 노드.
    """
    def __init__(self, master_pub_address: str, master_sync_address: str, dashboard_url: str):
        self.master_pub_address = master_pub_address
        self.master_sync_address = master_sync_address
        self.dashboard_url = dashboard_url # ✨ [추가] 대시보드 주소
        
        # ✨ [변경] target_hash는 더 이상 __init__에서 받지 않음
        self.target_hash = None
        self.worker_id = f"worker-{uuid.uuid4().hex[:6]}"
        self.status = "Initializing" # ✨ [추가] 워커의 현재 상태

        self._setup_logging()

        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)
        self.sync_client = self.context.socket(zmq.REQ)
        
        self.john_process = None
        self.hash_file = None

    def _setup_logging(self):
        logging.basicConfig(level=logging.INFO, format=f'%(asctime)s - {self.worker_id} - %(levelname)s - %(message)s', stream=sys.stdout)
        self.logger = logging.getLogger(__name__)

    def run(self):
        self.logger.info(f"Worker node starting (Engine: John the Ripper)...")
        
        # ✨ [추가] 백그라운드에서 heartbeat 전송 시작
        heartbeat_thread = Thread(target=self._send_heartbeat_periodically, daemon=True)
        heartbeat_thread.start()

        try:
            self._connect_and_subscribe()
            self._synchronize_with_master()
            self._listen_for_commands()
        except KeyboardInterrupt:
            self.logger.warning("Worker stopped by user.")
        except Exception as e:
            self.logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        finally:
            self._cleanup()

    # ✨ [추가] 주기적으로 자신의 상태를 대시보드에 보고하는 메서드
    def _send_heartbeat_periodically(self):
        """10초마다 대시보드의 /heartbeat 엔드포인트로 현재 상태를 POST합니다."""
        while True:
            try:
                payload = {
                    'worker_id': self.worker_id,
                    'status': self.status
                }
                requests.post(f"{self.dashboard_url}/heartbeat", json=payload, timeout=3)
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Could not send heartbeat to dashboard: {e}")
            time.sleep(10)

    def _connect_and_subscribe(self):
        self.subscriber.connect(self.master_pub_address)
        self.subscriber.setsockopt(zmq.SUBSCRIBE, b'control')
        self.subscriber.setsockopt(zmq.SUBSCRIBE, b'candidates')
        self.logger.info(f"Subscribed to topics on {self.master_pub_address}")

    def _synchronize_with_master(self):
        self.sync_client.connect(self.master_sync_address)
        self.logger.info(f"Sending 'READY' signal to master...")
        self.sync_client.send_string(self.worker_id)
        
        ack = self.sync_client.recv_string()
        if ack == "ACK":
            self.logger.info("Successfully synchronized with master. Waiting for START signal.")
            self.status = "Ready / Idle" # ✨ [상태 변경] 동기화 완료 후 대기 상태
        else:
            raise Exception("Failed to get ACK from master.")

    def _listen_for_commands(self):
        is_started = False
        while True:
            topic, content_bytes = self.subscriber.recv_multipart()
            content = content_bytes.decode('utf-8')

            if topic == b'control':
                # ✨ [변경] START 신호와 함께 해시 값을 수신
                if content.startswith('START:') and not is_started:
                    # 'START:' 뒷부분이 해시 값임
                    self.target_hash = content.split(':', 1)[1]
                    self.logger.info(f"START signal received with hash: {self.target_hash}")
                    self.status = "Cracking" # ✨ [상태 변경] 크랙 시작
                    is_started = True
                    self._start_john()
                elif content == 'stop':
                    self.logger.info("STOP signal received. Shutting down.")
                    self.status = "Idle" # ✨ [상태 변경] 중지 후 대기 상태
                    break
            
            elif topic == b'candidates' and is_started:
                # (이하 로직은 동일)
                if self.john_process and self.john_process.poll() is None:
                    try:
                        self.john_process.stdin.write(content + '\n')
                        self.john_process.stdin.flush()
                    except BrokenPipeError:
                        self.logger.info("John process pipe broke, likely found the password.")
                        self.status = "Finished / Idle"
                        break
                elif self.john_process:
                     self.logger.warning("John process terminated unexpectedly.")
                     self.status = "Error"
                     break

    def _start_john(self):
        """임시 해시 파일을 생성하고 John the Ripper 자식 프로세스를 시작합니다."""
        try:
            self.hash_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix=".hash")
            self.hash_file.write(self.target_hash)
            self.hash_file.close()
            self.logger.info(f"Created temporary hash file at: {self.hash_file.name}")
            
            john_cmd = ['john', '--stdin', '--format=raw-md5', self.hash_file.name]
            
            # 아래 라인의 'PIPE'가 이제 정상적으로 인식됩니다.
            self.john_process = subprocess.Popen(john_cmd, stdin=PIPE, text=True, stdout=sys.stdout, stderr=sys.stderr)
        except Exception as e:
            self.logger.error(f"Failed to start John the Ripper: {e}")
            self.status = "Error"
            # self._cleanup() # ✨ [개선] 여기서 cleanup을 호출하면 연쇄 오류가 발생하므로 주석 처리


    def _cleanup(self):
        # (이하 로_listen_for_commands 동일)
        self.status = "Offline"
        self.logger.info("Shutting down worker...")
        if self.john_process and self.john_process.poll() is None:
            self.john_process.terminate()
            self.john_process.wait(timeout=5)
        if self.hash_file and os.path.exists(self.hash_file.name):
            try:
                os.remove(self.hash_file.name)
                self.logger.info(f"Removed temporary hash file: {self.hash_file.name}")
            except OSError as e:
                self.logger.error(f"Error removing temporary file {self.hash_file.name}: {e}")
        self.subscriber.close()
        self.sync_client.close()
        self.context.term()

if __name__ == "__main__":
    # ✨ [변경] 더 이상 .env를 사용하지 않고, 대시보드 주소만 설정'
    IP = '127.0.0.1'
    DASHBOARD_URL = "http://"+IP+":5001"
    
    worker = WorkerNode(
        master_pub_address="tcp://"+IP+":5555",
        master_sync_address="tcp://"+IP+":5556",
        dashboard_url=DASHBOARD_URL
    )
    worker.run()