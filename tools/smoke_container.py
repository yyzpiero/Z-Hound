"""Check a locally built image with an empty, disposable data mount; no API keys.

    python3 tools/smoke_container.py --image z-hound:local

Uses Docker CLI, never changes ./data, and removes its own container on exit.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid


def check_dashboard(request):
    """Exercise startup and real seed import/review endpoints without API calls."""
    health = request('/api/health')
    assert health['ok'] and health['data_dir_accessible'], health
    config = request('/api/hunting/config')
    assert not config['fofa_configured'] and not config['deepseek_configured']
    assert request('/api/hunts')['items'] == [], 'Image must start without tasks'
    assert request('/api/hunting/seeds')['items'] == [], 'Image must start without seeds'
    seed = request('/api/hunting/seeds/import', {'items': [{
        'content': '192.0.2.10', 'source': 'Disposable container smoke check',
        'evidence': 'Reserved documentation address, never queried'}]})['items'][0]
    assert not seed['eligible']
    reviewed = request('/api/hunting/seeds/' + seed['id'] + '/review', {
        'revision': seed['qualification']['revision'], 'status': 'confirmed',
        'value': 'high', 'reviewer': 'Container smoke check',
        'rationale': 'Verify seed review and persistent storage only',
        'source': seed['source'], 'evidence': seed['evidence']})
    assert reviewed['eligible']
    assert request('/api/hunting/seeds')['items'][0]['eligible']
    assert request('/api/hunts')['items'] == [], 'Smoke check must not start a hunt'
    return {'version': health['version'], 'empty_initial_state': True,
            'seed_import_and_review': True, 'external_api_calls': 0}


def wait_for_response(request, path, timeout):
    """Docker may accept TCP before the app is ready, then reset/disconnect."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return request(path)
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.RemoteDisconnected):
            if time.monotonic() >= deadline:
                raise RuntimeError('Container did not become ready in time') from None
            time.sleep(1)


def run(image, timeout):
    if not shutil.which('docker'):
        raise RuntimeError('Docker CLI is required; install/start Docker before this check')
    name = 'z-hound-smoke-' + uuid.uuid4().hex[:12]
    names = [name]
    with tempfile.TemporaryDirectory(prefix='z-hound-smoke-') as directory:
        def start(container_name):
            command = ['docker', 'run', '--detach', '--rm', '--name', container_name,
                       '--publish', '127.0.0.1::8080', '--volume', str(Path(directory).resolve()) + ':/app/data']
            if hasattr(os, 'getuid'):
                command += ['--user', str(os.getuid()) + ':' + str(os.getgid())]
            subprocess.run(command + [image], check=True, capture_output=True, text=True)
            address = subprocess.check_output(['docker', 'port', container_name, '8080/tcp'], text=True).strip()
            return 'http://' + address

        try:
            base = start(name)
            def request(path, payload=None):
                data = None if payload is None else json.dumps(payload).encode()
                req = urllib.request.Request(base + path, data=data,
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.load(response)
            wait_for_response(request, '/api/health', timeout)
            result = check_dashboard(request)
            subprocess.run(['docker', 'restart', name], check=True, capture_output=True)
            seeds = wait_for_response(request, '/api/hunting/seeds', timeout)['items']
            assert len(seeds) == 1 and seeds[0]['eligible'], 'Restart lost seed review'
            result['restart_preserves_review'] = True
            subprocess.run(['docker', 'stop', '--time', '5', name], check=True, capture_output=True)
            names.append(name + '-recreated')
            base = start(names[-1])
            seeds = wait_for_response(request, '/api/hunting/seeds', timeout)['items']
            assert len(seeds) == 1 and seeds[0]['eligible'], 'Container recreation lost seed review'
            assert len(seeds[0]['history']) == 1, 'Container recreation lost review history'
            assert request('/api/hunts')['items'] == []
            result['recreated_container_preserves_review'] = True
            return result
        finally:
            for container_name in names:
                subprocess.run(['docker', 'stop', '--time', '5', container_name], capture_output=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--timeout', type=int, default=90)
    args = parser.parse_args()
    print(json.dumps(run(args.image, args.timeout), indent=2))
