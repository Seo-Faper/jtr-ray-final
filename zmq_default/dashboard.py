from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from threading import Thread, Lock
import time
import zmq 
import itertools
from datetime import datetime
import logging

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 컨트롤러 클래스 정의 ---
class MasterController:
    """크래킹 작업의 상태와 로직을 모두 관리하는 중앙 컨트롤러"""

    def __init__(self, pub_port=5555, sync_port=5556):
        self.lock = Lock()
        
        # ZMQ 설정
        self.context = zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.bind(f"tcp://*:{pub_port}")
        self.sync_socket = self.context.socket(zmq.REP)
        self.sync_socket.bind(f"tcp://*:{sync_port}")

        # 작업 상태 변수
        self.target_hash = None
        self.charset = 'abcdefghijklmnopqrstuvwxyz0123456789'
        self.max_length = 6
        self.job_status = "WAITING"  # WAITING, RUNNING, STOPPED, FINISHED
        self.total_sent_count = 0
        self.start_time = 0
        self.last_candidate = ""
        self.error_message = None

        # 작업 스레드
        self.job_thread = None

    def start_job(self, target_hash):
        """새로운 크래킹 작업을 시작합니다."""
        with self.lock:
            if self.job_status == "RUNNING":
                self.error_message = "A job is already running."
                logging.warning(self.error_message)
                return False

            self.target_hash = target_hash
            self.job_status = "RUNNING"
            self.total_sent_count = 0
            self.start_time = time.time()
            self.error_message = None
            
            # 별도 스레드에서 작업 분배 실행
            self.job_thread = Thread(target=self._run_task_broadcast, daemon=True)
            self.job_thread.start()
            logging.info(f"Job started for hash: {self.target_hash}")
            return True

    def stop_job(self):
        """실행 중인 작업을 중지합니다."""
        with self.lock:
            if self.job_status != "RUNNING":
                self.error_message = "No job is currently running."
                logging.warning(self.error_message)
                return False

            self.job_status = "STOPPED"
            logging.info("Job stop requested by user.")
        
        # 모든 워커에게 중지 신호 방송
        self.publisher.send_multipart([b'control', b'stop'])
        return True

    def _run_task_broadcast(self):
        """[스레드 대상] 패스워드 후보군을 생성하고 워커에게 방송합니다."""
        logging.info("Task broadcasting thread started.")
        
        # ✨ [변경] START 신호와 함께 타겟 해시를 전송
        start_command = f'START:{self.target_hash}'.encode('utf-8')
        self.publisher.send_multipart([b'control', start_command])
        
        time.sleep(0.5)

        # 길이 1부터 순차적으로 후보군 생성
        for length in range(1, self.max_length + 1):
            candidates = itertools.product(self.charset, repeat=length)
            for candidate_tuple in candidates:
                # stop_job 메서드에 의해 상태가 변경되었는지 확인
                with self.lock:
                    if self.job_status != "RUNNING":
                        logging.info("Broadcasting thread is stopping.")
                        return # 스레드 종료

                candidate = "".join(candidate_tuple)
                self.publisher.send_multipart([b'candidates', candidate.encode('utf-8')])
                
                with self.lock:
                    self.total_sent_count += 1
                    self.last_candidate = candidate
        
        # 모든 후보군 생성 완료
        with self.lock:
            if self.job_status == "RUNNING":
                self.job_status = "FINISHED"
                logging.info("Job finished: all candidates sent.")
        self.publisher.send_multipart([b'control', b'stop'])

    def get_status(self):
        """UI 렌더링을 위한 현재 상태 정보를 반환합니다."""
        with self.lock:
            status_data = {
                'target_hash': self.target_hash,
                'job_status': self.job_status,
                'total_sent_count': self.total_sent_count,
                'last_candidate': self.last_candidate,
                'error_message': self.error_message,
                'elapsed_time': 0,
                'speed': 0
            }
            if self.job_status == "RUNNING" and self.start_time > 0:
                elapsed = time.time() - self.start_time
                status_data['elapsed_time'] = f"{elapsed:.2f}s"
                status_data['speed'] = f"{self.total_sent_count / elapsed:,.0f} c/s" if elapsed > 0 else 0
            
            # 다음 요청을 위해 에러 메시지 초기화
            self.error_message = None
            return status_data

# --- Flask 앱 설정 ---
app = Flask(__name__)
app.secret_key = 'a_very_secret_key_for_flash_messages'

# --- 전역 컨트롤러 및 워커 상태 변수 ---
master_controller = MasterController()
workers_lock = Lock()
WORKERS = {}

# --- Jinja2 필터 설정 ---
def format_datetime(timestamp):
    return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
app.jinja_env.filters['strftime'] = format_datetime

# --- 라우트(Routes) 정의 ---
@app.route('/')
def index():
    """메인 대시보드 페이지"""
    with workers_lock:
        # 워커 목록을 마지막으로 본 시간 순으로 정렬
        sorted_workers = dict(sorted(WORKERS.items(), key=lambda item: item[1]['last_seen'], reverse=True))
    return render_template('index.html', workers=sorted_workers, master_status=master_controller.get_status())

@app.route('/start', methods=['POST'])
def start_job_route():
    """작업 시작 요청을 처리합니다."""
    target_hash = request.form.get('target_hash')
    if not target_hash:
        flash("Target hash is required.", "error")
    elif master_controller.start_job(target_hash):
        flash(f"Cracking job started for hash: {target_hash}", "success")
    else:
        # 컨트롤러에서 설정된 에러 메시지를 가져와 표시
        status = master_controller.get_status()
        flash(status.get('error_message', 'Failed to start job.'), "error")
    return redirect(url_for('index'))

@app.route('/stop', methods=['POST'])
def stop_job_route():
    """작업 중지 요청을 처리합니다."""
    if master_controller.stop_job():
        flash("Job stop signal sent.", "info")
    else:
        status = master_controller.get_status()
        flash(status.get('error_message', 'Failed to stop job.'), "error")
    return redirect(url_for('index'))

@app.route('/heartbeat', methods=['POST'])
def heartbeat():
    """워커로부터 하트비트를 수신하고, 요청 IP를 함께 기록합니다."""
    data = request.json
    worker_id = data.get('worker_id')
    if not worker_id:
        return jsonify({"status": "error", "message": "worker_id is required"}), 400

    # ✨ [추가] 프록시 서버(예: Nginx) 뒤에 앱이 있을 경우를 대비해 실제 IP를 찾음
    # 직접 실행 환경에서는 request.remote_addr만으로 충분합니다.
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)

    with workers_lock:
        # 기존 정보에 IP 주소 추가
        WORKERS[worker_id] = {
            'last_seen': time.time(),
            'status': data.get('status', 'Active'),
            'ip_address': ip_address # ✨ [추가]
        }
    return jsonify({"status": "ok"})

# --- 백그라운드 스레드 함수 ---
def sync_server_thread():
    """[스레드 대상] 워커의 연결 및 준비 신호를 계속해서 받습니다."""
    logging.info("Sync server thread started.")
    while True:
        try:
            worker_id = master_controller.sync_socket.recv_string()
            master_controller.sync_socket.send_string("ACK")
            logging.info(f"Worker '{worker_id}' synchronized.")
        except zmq.error.ContextTerminated:
            break # 메인 앱 종료 시 스레드 종료

def cleanup_dead_workers_thread():
    """오랫동안 하트비트가 없는 워커를 정리합니다."""
    logging.info("Dead worker cleanup thread started.")
    while True:
        time.sleep(30)
        with workers_lock:
            now = time.time()
            dead_workers = [w_id for w_id, data in WORKERS.items() if now - data['last_seen'] > 60]
            for w_id in dead_workers:
                logging.warning(f"Worker {w_id} seems to be offline. Removing.")
                del WORKERS[w_id]

# --- 앱 실행 ---
if __name__ == '__main__':
    # 백그라운드 스레드들 시작
    Thread(target=sync_server_thread, daemon=True).start()
    Thread(target=cleanup_dead_workers_thread, daemon=True).start()
    
    # Flask 앱 실행
    app.run(host='0.0.0.0', port=5001)