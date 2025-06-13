import zmq
import itertools
import time
import sys
import logging
from typing import Iterator, Set

class MasterNode:
    """
    워커와 동기화 후, 사용자 명령에 따라 작업을 분배하는 마스터 노드.
    - 5555번 포트 (PUB): 워커에게 작업/제어 신호를 방송합니다.
    - 5556번 포트 (REP): 워커의 준비 상태를 확인하고 동기화합니다.
    """
    def __init__(self, pub_address: str, sync_address: str, charset: str, max_length: int):
        self.pub_address = pub_address
        self.sync_address = sync_address
        self.charset = charset
        self.max_length = max_length

        self._setup_logging()

        self.context = zmq.Context()
        # 1. 작업/제어 신호 방송용 소켓
        self.publisher = self.context.socket(zmq.PUB)
        # 2. 워커 동기화용 소켓
        self.sync_socket = self.context.socket(zmq.REP)
        
        self.total_sent_count = 0
        self.start_time = 0.0
        self.ready_workers: Set[str] = set()

    def _setup_logging(self) -> None:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
        self.logger = logging.getLogger(__name__)

    def _generate_candidates(self) -> Iterator[str]:
        for length in range(1, self.max_length + 1):
            self.logger.info(f"Now generating candidates of length {length}...")
            for candidate_tuple in itertools.product(self.charset, repeat=length):
                yield "".join(candidate_tuple)

    def run(self) -> None:
        """마스터 노드의 메인 로직을 실행합니다."""
        self.logger.info("Master node is starting...")
        self.publisher.bind(self.pub_address)
        self.sync_socket.bind(self.sync_address)
        
        try:
            self._wait_for_workers_and_command()
            self._broadcast_tasks()
        except KeyboardInterrupt:
            self.logger.warning("Master stopped by user.")
        finally:
            self._broadcast_stop_signal()
            self._cleanup()

    def _wait_for_workers_and_command(self):
        """워커들이 연결하고 사용자가 시작 명령을 내릴 때까지 대기합니다."""
        self.logger.info(f"Waiting for workers to connect on {self.sync_address}...")
        
        poller = zmq.Poller()
        poller.register(self.sync_socket, zmq.POLLIN)
        poller.register(sys.stdin, zmq.POLLIN)

        print("\n" + "="*50)
        print("  SYSTEM IS IN WAITING STATE.")
        print("  - Workers can connect now.")
        print("  - Press [Enter] to start distributing tasks to connected workers.")
        print("="*50)

        while True:
            socks = dict(poller.poll())

            if self.sync_socket in socks:
                worker_id = self.sync_socket.recv_string()
                self.sync_socket.send_string("ACK") # 확인(Acknowledge) 응답
                self.ready_workers.add(worker_id)
                self.logger.info(f"Worker '{worker_id}' is ready. Total ready workers: {len(self.ready_workers)}")

            if sys.stdin.fileno() in socks:
                input() # Enter 키 입력을 소비
                if not self.ready_workers:
                    self.logger.warning("No workers are ready. Please connect workers before starting.")
                    continue
                self.logger.info(f"User command received! Starting task distribution to {len(self.ready_workers)} workers.")
                break
    
    def _broadcast_tasks(self):
        """모든 워커에게 시작 신호를 보내고 작업을 방송합니다."""
        self.logger.info("Broadcasting 'START' signal to all workers...")
        self.publisher.send_multipart([b'control', b'START'])
        time.sleep(1) # 워커들이 START 신호를 수신할 시간

        self.start_time = time.time()
        for candidate in self._generate_candidates():
            self.total_sent_count += 1
            self.publisher.send_multipart([b'candidates', candidate.encode('utf-8')])
            if self.total_sent_count % 100000 == 0:
                self._print_status(candidate)
        
        self.logger.info("Exhausted all candidates.")

    def _print_status(self, last_candidate: str):
        elapsed_time = time.time() - self.start_time
        speed = self.total_sent_count / elapsed_time if elapsed_time > 0 else 0
        status_line = (f"  -> Last sent: {last_candidate:<10} | Total: {self.total_sent_count/1_000_000:.2f}M | Speed: {speed:,.0f} c/s")
        print(status_line, end='\r', flush=True)

    def _broadcast_stop_signal(self):
        self.logger.info("Broadcasting 'stop' signal to all workers...")
        self.publisher.send_multipart([b'control', b'stop'])
        time.sleep(1)

    def _cleanup(self):
        self.logger.info("Shutting down ZMQ resources.")
        self.publisher.close()
        self.sync_socket.close()
        self.context.term()
        print("\nMaster node has shut down.")

if __name__ == "__main__":
    master = MasterNode(
        pub_address="tcp://*:5555",
        sync_address="tcp://*:5556",
        charset='abcdefghijklmnopqrstuvwxyz',
        max_length=6
    )
    master.run()