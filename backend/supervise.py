#!/usr/bin/env python3
import signal
import subprocess
import sys
import time

def main():
    for args in [['migrate', '--run-syncdb', '--noinput'], ['bootstrap']]:
        subprocess.run([sys.executable, 'manage.py', *args], check=True)
    processes = [subprocess.Popen(['gunicorn', '--bind=0.0.0.0:8000', '--workers=1', '--threads=4', '--access-logfile=-', 'config.wsgi:application']),
                 subprocess.Popen([sys.executable, 'manage.py', 'reconcile'])]
    stopping = []
    def stop(sig, frame): stopping.append(sig)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping and all(p.poll() is None for p in processes):
        time.sleep(.5)
    failed = not stopping
    for p in processes:
        if p.poll() is None: p.terminate()
    for p in processes:
        try: p.wait(timeout=35)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    return 1 if failed else 0

if __name__ == '__main__': sys.exit(main())
