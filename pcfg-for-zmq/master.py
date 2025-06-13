import zmq
import time
import itertools
import math
import logging
import random # ✨ [추가] 분배 계획을 섞기 위해 추가
from threading import Thread, RLock

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('Master')

# ==============================================================================
# 모듈 1: 상태 관리 모듈 (StateManager)
# - 변경 사항 없음 -
# ==============================================================================
class StateManager:
    """시스템의 모든 상태를 중앙에서 관리하고 동기화를 보장합니다."""
    def __init__(self):
        self._lock = RLock()
        self.job_status = "WAITING"  # WAITING, RUNNING, STOPPED, FOUND, FINISHED
        self.target_hash = None
        self.start_time = 0
        self.end_time = 0
        self.found_password = None
        self.total_sent_count = 0
        self.workers = {}  # { 'worker_id': {'identity': b'..', 'status': '...'} }

    def start_job(self, target_hash):
        with self._lock:
            if self.job_status == "RUNNING":
                logger.warning("A job is already running.")
                return False
            if not self.get_active_workers():
                logger.error("No active workers available to start the job.")
                return False
                
            self.job_status = "RUNNING"
            self.target_hash = target_hash
            self.start_time = time.time()
            self.end_time = 0
            self.found_password = None
            self.total_sent_count = 0
            logger.info(f"Job state changed to RUNNING for hash: {target_hash}")
            return True

    def stop_job(self, final_status="STOPPED"):
        with self._lock:
            if self.job_status != "RUNNING":
                return False
            self.job_status = final_status
            self.end_time = time.time()
            logger.info(f"Job state changed to {final_status}")
            return True

    def report_found(self, password, worker_id):
        with self._lock:
            if self.job_status != "RUNNING":
                return
            self.found_password = password
            logger.info(f"Password '{password}' found by worker '{worker_id}'.")
            self.stop_job(final_status="FOUND")

    def register_worker(self, worker_id, identity):
        with self._lock:
            self.workers[worker_id] = {'identity': identity, 'status': 'READY'}
            logger.info(f"Worker '{worker_id}' registered and ready.")

    def increment_sent_count(self, count):
        with self._lock:
            self.total_sent_count += count
            
    def get_job_status(self):
        with self._lock:
            return self.job_status

    def get_active_workers(self):
        with self._lock:
            return [{'id': w_id, **w_data} for w_id, w_data in self.workers.items() if w_data.get('identity')]

    def get_status_snapshot(self):
        with self._lock:
            elapsed = 0
            if self.start_time > 0:
                effective_end_time = self.end_time if self.job_status != "RUNNING" else time.time()
                elapsed = effective_end_time - self.start_time

            speed = self.total_sent_count / elapsed if elapsed > 0 else 0
            
            return {
                "job_status": self.job_status,
                "target_hash": self.target_hash,
                "found_password": self.found_password,
                "active_workers": len(self.get_active_workers()),
                "total_sent": f"{self.total_sent_count:,}",
                "elapsed_time": f"{elapsed:.2f}s",
                "speed_cps": f"{speed:,.0f}"
            }

# ==============================================================================
# 실시간 생성 모듈 (CandidateGenerator)
# ==============================================================================
class CandidateGenerator:
    def __init__(self, charset, max_length):
        self.charset = charset
        self.max_length = max_length

    def __iter__(self):
        for length in range(1, self.max_length + 1):
            for candidate_tuple in itertools.product(self.charset, repeat=length):
                yield "".join(candidate_tuple)

# ==============================================================================
# 노드 분배 규칙 지정 모듈 (DistributionPlanner)
# ==============================================================================
class DistributionPlanner:
    """
    지정된 규칙에 따라 워커들에게 작업을 분배할 계획을 수립합니다.
    규칙은 동적으로 변경될 수 있습니다.
    """
    def __init__(self, initial_rule):
        self._lock = RLock()
        self.rule = initial_rule
        logger.info(f"Distribution planner initialized with rule: {self.rule}")

    def update_rule(self, new_rule):
        """새로운 분배 규칙으로 업데이트합니다."""
        with self._lock:
            self.rule = new_rule
            logger.info(f"Distribution rule updated to: {self.rule}")

    def create_dispatch_plan(self, active_workers):
        """
        활성 워커 목록과 현재 규칙을 기반으로 작업 '배치'를 보낼 순서(리스트)를 생성합니다.
        성능 기반(gpu, cpu) 대신 워커 ID 기반으로 단순화됩니다.
        """
        with self._lock:
            if not active_workers:
                return []

            worker_ratios = {}
            # 워커 ID에 직접 할당된 비율을 사용하고, 없으면 'default' 값을 사용합니다.
            for worker in active_workers:
                w_id = worker['id']
                ratio = self.rule.get(w_id, self.rule.get('default', 1))
                worker_ratios[w_id] = ratio

            ratios = [r for r in worker_ratios.values() if r > 0]
            if not ratios:
                return []
                
            common_divisor = math.gcd(*ratios)
            
            dispatch_plan = []
            for worker in active_workers:
                w_id = worker['id']
                identity = worker['identity']
                # 정규화된 비율만큼 워커를 목록에 추가합니다.
                count = worker_ratios.get(w_id, 0) // common_divisor
                dispatch_plan.extend([identity] * count)
            
            
           #logger.info(f"Dispatch plan created (cycle size: {len(dispatch_plan)}). Rule: {self.rule}")
            return dispatch_plan

# ==============================================================================
# ✨ [수정] 메인 오케스트레이터 (Master)
# - 작업 루프 및 명령어 처리 로직 수정 -
# ==============================================================================
class Master:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger('MasterOrchestrator')
        self.state = StateManager()
        # ✨ [변경] Planner를 동적으로 관리하도록 초기 규칙을 전달합니다.
        self.planner = DistributionPlanner(config['distribution_rule'])
        self.context = zmq.Context()
        self.router_socket = self.context.socket(zmq.ROUTER)
        self.router_socket.bind(f"tcp://*:{config['port']}")
        self.job_thread = None

    def _handle_worker_messages(self):
        self.logger.info("Worker message listener started.")
        while True:
            try:
                identity, command_bytes, *payload = self.router_socket.recv_multipart()
                command = command_bytes.decode('utf-8')

                if command == 'READY':
                    worker_id = payload[0].decode('utf-8')
                    self.state.register_worker(worker_id, identity)
                elif command == 'FOUND':
                    password = payload[0].decode('utf-8')
                    worker_id = payload[1].decode('utf-8')
                    self.state.report_found(password, worker_id)

            except zmq.error.ContextTerminated:
                break
            except Exception as e:
                self.logger.error(f"Error handling worker message: {e}")

    def _run_job(self):
        """
        ✨ [핵심 수정] 작업 실행 루프입니다.
        이제 이 루프는 주기적으로 분배 계획을 다시 생성하여 동적 규칙 변경과
        워커의 추가/이탈에 실시간으로 대응합니다.
        """
        target_hash = self.state.target_hash
        self.logger.info("Job thread starting...")
        
        # 모든 활성 워커에게 작업을 시작하라는 신호를 한 번만 보냅니다.
        active_workers_on_start = self.state.get_active_workers()
        start_command = f'START:{target_hash}'.encode('utf-8')
        for worker in active_workers_on_start:
            self.router_socket.send_multipart([worker['identity'], b'control', start_command])

        candidate_iterator = iter(CandidateGenerator(self.config['charset'], self.config['max_length']))

        # 작업이 RUNNING 상태인 동안 계속 루프를 돕니다.
        while self.state.get_job_status() == "RUNNING":
            current_active_workers = self.state.get_active_workers()
            if not current_active_workers:
                self.logger.error("No active workers. Stopping job.")
                self.state.stop_job()
                break

            # ✨ 현재 워커 상태와 규칙에 따라 새로운 분배 계획을 생성합니다.
            dispatch_plan = self.planner.create_dispatch_plan(current_active_workers)
            if not dispatch_plan:
                self.logger.warning("Dispatch plan is empty. Retrying...")
                time.sleep(2)
                continue

            # 생성된 계획에 따라 한 사이클의 배치를 분배합니다.
            for worker_identity in dispatch_plan:
                if self.state.get_job_status() != "RUNNING":
                    break # 상태가 바뀌면 즉시 중단

                # 설정된 배치 크기만큼 후보군을 가져옵니다.
                batch = list(itertools.islice(candidate_iterator, self.config['batch_size']))

                if not batch: # 생성기에서 더 이상 가져올 후보군이 없는 경우
                    if self.state.get_job_status() == "RUNNING":
                         self.state.stop_job(final_status="FINISHED")
                    break
                
                # 워커에게 후보군 배치 전송
                payload = "\n".join(batch).encode('utf-8')
                self.router_socket.send_multipart([worker_identity, b'candidates', payload])
                self.state.increment_sent_count(len(batch))
        
        self.logger.info("Job thread finished.")


    def start(self):
        listener_thread = Thread(target=self._handle_worker_messages, daemon=True)
        listener_thread.start()
        self.logger.info("Master is running. Enter commands: start <hash>, stop, status, set_rule, exit")
        while True:
            try:
                cmd_input = input("> ").strip().split()
                if not cmd_input: continue
                command = cmd_input[0].lower()

                if command == 'start':
                    if len(cmd_input) < 2:
                        print("Usage: start <sha256_hash>")
                        continue
                    target_hash = cmd_input[1]
                    if self.state.start_job(target_hash):
                        self.job_thread = Thread(target=self._run_job, daemon=True)
                        self.job_thread.start()
                
                elif command == 'stop':
                    active_workers_before_stop = self.state.get_active_workers()
                    if self.state.stop_job():
                        for worker in active_workers_before_stop:
                            try:
                                self.router_socket.send_multipart([worker['identity'], b'control', b'stop'])
                            except zmq.ZMQError as e:
                                self.logger.warning(f"Failed to send stop signal to {worker['id']}: {e}")
                    else:
                        print("No job is currently running.")
                
                elif command == 'status':
                    print(self.state.get_status_snapshot())

                elif command == 'set_rule':
                    if len(cmd_input) < 2:
                        print("Usage: set_rule <worker_id1>:<ratio1> [worker_id2:ratio2 ...]")
                        print("Example: set_rule worker-01:3 worker-02:5 default:1")
                        continue
                    try:
                        new_rule = {}
                        for item in cmd_input[1:]:
                            key, value = item.split(':', 1)
                            new_rule[key.strip()] = int(value.strip())
                        self.planner.update_rule(new_rule)
                        print(f"Distribution rule updated successfully.")
                    except (ValueError, IndexError):
                        print("Invalid format. Use 'key:integer_value' pairs.")

                elif command == 'exit':
                    break
                else:
                    print(f"Unknown command: {command}")
            except (KeyboardInterrupt, EOFError):
                break
        
        self.logger.info("Master shutting down.")
        self.context.term()

if __name__ == '__main__':
    master_config = {
        'port': 5555,
        'charset': 'abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ~!@#$%^&*()_+',
        'max_length': 6,
        'batch_size': 20000,
        'distribution_rule': {
            'default': 1, 
        }
    }
    master = Master(master_config)
    master.start()