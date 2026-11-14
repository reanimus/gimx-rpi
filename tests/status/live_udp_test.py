"""Exercise the installed GIMX binary without Bluetooth hardware (remote transport)."""
import socket
import subprocess
import threading
import time
from gimx_status import query_status, StatusUnavailable


with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
    peer.bind(('127.0.0.1', 0))
    peer.settimeout(0.1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(('127.0.0.1', 0))
        listen_port = reservation.getsockname()[1]
    stop = threading.Event()
    received_input = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                packet, address = peer.recvfrom(65535)
            except socket.timeout:
                continue
            if packet == b'\x00':
                peer.sendto(b'\x00\x05', address)  # DS4 controller type.
            elif packet[:1] == b'\x01':
                received_input.set()

    worker = threading.Thread(target=serve)
    worker.start()
    with open('/tmp/gimx-live-status.log', 'w') as log:
        process = subprocess.Popen([
            '/usr/local/bin/gimx', '--src', f'127.0.0.1:{listen_port}',
            '--dst', f'127.0.0.1:{peer.getsockname()[1]}'], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 5
            while True:
                assert process.poll() is None, 'GIMX exited; see /tmp/gimx-live-status.log'
                try:
                    status = query_status(port=listen_port, timeout=0.1)
                    break
                except StatusUnavailable:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.05)
            assert not status.supported and not status.ready and status.controller_type == 5
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                client.connect(('127.0.0.1', listen_port))
                client.send(b'\x00')
                assert client.recv(100) == b'\x00\x05'
                client.send(bytes([1, 1, 137, 0, 0, 0, 255]))
                assert received_input.wait(1)
            print('PASS: installed GIMX answers status over UDP (unsupported remote transport), legacy query and input remain functional')
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            stop.set()
            worker.join()
