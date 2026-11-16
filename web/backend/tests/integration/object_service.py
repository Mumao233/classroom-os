"""Owned loopback SeaweedFS instances for object-store fault injection.

Each instance has its own data directory and ports and can be stopped and started
again on the same data, which is how PR-04b tests take the media store or the
independent ledger store away while a deletion is in flight. Synthetic data only.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import httpx

ACCESS, SECRET, REGION = 'pr04b-synthetic', 'pr04b-synthetic-secret', 'us-east-1'


def _ports():
    # SeaweedFS derives each gRPC port as HTTP + 10000; reserve both ranges together.
    while True:
        first = 20000 + secrets.randbelow(20000)
        selected = [first + offset for offset in range(4)]
        sockets = []
        try:
            for value in selected + [value + 10000 for value in selected]:
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(('127.0.0.1', value))
            return selected
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()


class ObjectService:
    def __init__(self, directory: Path, name: str, weed: str | None = None, *, access=ACCESS, secret=SECRET, volumes=64):
        self.access, self.secret = access, secret
        self.weed = weed or os.environ.get('PR04B_WEED')
        if not self.weed or not Path(self.weed).is_file():
            raise RuntimeError('PR04B_WEED must name the prepared SeaweedFS binary')
        self.name, self.root = name, Path(directory) / name
        self.root.mkdir(parents=True)
        (self.root / 'data').mkdir()
        self.ports = _ports()
        self.endpoint = f'http://127.0.0.1:{self.ports[3]}'
        self.process = None
        self.starts = 0
        config = self.root / 's3.json'
        config.write_text(json.dumps({'identities': [{'name': 'synthetic-only', 'credentials': [
            {'accessKey': access, 'secretKey': secret}], 'actions': ['Admin', 'Read', 'Write', 'List', 'Tagging']}]}),
            encoding='utf8')
        master, volume, filer, s3 = self.ports
        self.args = [self.weed, 'server', '-ip=127.0.0.1', '-ip.bind=127.0.0.1', '-dir=' + str(self.root / 'data'),
                     f'-master.port={master}', f'-volume.port={volume}', '-filer', f'-filer.port={filer}',
                     '-s3', f'-s3.port={s3}', '-s3.config=' + str(config), f'-volume.max={volumes}',
                     '-master.volumeSizeLimitMB=64', '-master.telemetry=false',
                     '-s3.port.iceberg=0', '-s3.port.lance=0', '-s3.iam=false']

    def client(self):
        return boto3.client('s3', endpoint_url=self.endpoint, region_name=REGION,
                            aws_access_key_id=self.access, aws_secret_access_key=self.secret,
                            config=Config(connect_timeout=1, read_timeout=5, retries={'total_max_attempts': 1},
                                          s3={'addressing_style': 'path'}, request_checksum_calculation='when_required',
                                          response_checksum_validation='when_required'))

    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        if self.running():
            return self
        self.starts += 1
        log = (self.root / f'seaweed-{self.starts}.log').open('wb')
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PG', 'AWS'))}
        self.process = subprocess.Popen(self.args, cwd=self.root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        log.close()
        (self.root / 'pid').write_text(str(self.process.pid), encoding='ascii')
        for _ in range(120):
            if self.process.poll() is not None:
                raise RuntimeError(f'{self.name} object service stopped before readiness')
            try:
                if httpx.get(self.endpoint, timeout=1).status_code in (200, 401, 403):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError(f'{self.name} object service readiness timed out')
        probe = self.client()
        for _ in range(60):
            try:
                if not any(b['Name'] == 'pr04b-readiness' for b in probe.list_buckets().get('Buckets', [])):
                    probe.create_bucket(Bucket='pr04b-readiness')
                probe.put_object(Bucket='pr04b-readiness', Key='ready', Body=b'ready')
                return self
            except (ClientError, BotoCoreError):
                time.sleep(0.5)
        raise RuntimeError(f'{self.name} object service write readiness timed out')

    def stop(self):
        if self.running():
            self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)
        self.process = None
