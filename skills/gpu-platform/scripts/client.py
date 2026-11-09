#!/usr/bin/env python3
"""Standard-library client for the GPU platform's versioned API."""
import argparse
import getpass
from http.client import HTTPException
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ClientError(Exception):
    def __init__(self, result):
        self.result = result


class Client:
    def __init__(self, base, token=None):
        if not base:
            raise ClientError({'error': 'url_missing', 'message': 'Set GPU_PLATFORM_URL or pass --url with the platform site root.'})
        parts = urlsplit(base)
        if parts.scheme not in {'http', 'https'} or not parts.netloc or parts.username or parts.password or parts.path.rstrip('/') or parts.query or parts.fragment:
            raise ClientError({'error': 'invalid_url', 'message': 'GPU_PLATFORM_URL must be a site root such as https://gpu.example.edu'})
        self.base, self.token = base.rstrip('/'), token
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, data=None, key=None, query=(), output=None):
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or not path.startswith('/api/v1/') or '..' in parsed.path.split('/'):
            raise ClientError({'error': 'invalid_path', 'message': 'Use a path starting with /api/v1/'})
        if query:
            path += ('&' if '?' in path else '?') + urlencode(query)
        method = method.upper()
        key = key or (uuid.uuid4().hex if method == 'POST' and path != '/api/v1/auth/token' else None)
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer '+self.token
        if key:
            headers['Idempotency-Key'] = key
        raw = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        if raw is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(self.base+path, data=raw, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                if output is not None:
                    target = Path(output)
                    with target.open('wb') as stream:
                        while True:
                            chunk = response.read(1024*1024)
                            if not chunk:
                                break
                            stream.write(chunk)
                    return {'data': {'saved_to': str(target)}}
                return json.load(response)
        except HTTPError as exc:
            try:
                result = json.load(exc)
            except (ValueError, UnicodeDecodeError, HTTPException, OSError):
                result = {'error': {'message': exc.reason}}
            if not isinstance(result, dict):
                result = {'error': {'message': exc.reason}}
            result['http_status'] = exc.code
            if key:
                result['idempotency_key'] = key
            raise ClientError(result) from exc
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            result = {'error': 'network_error', 'message': str(exc)}
            if key:
                result['idempotency_key'] = key
            raise ClientError(result) from exc
        except (ValueError, UnicodeDecodeError) as exc:
            result = {'error': 'invalid_response', 'message': 'The server response was not JSON.'}
            if key:
                result['idempotency_key'] = key
            raise ClientError(result) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('GPU_PLATFORM_URL'))
    commands = parser.add_subparsers(dest='command', required=True)
    request = commands.add_parser('request')
    request.add_argument('method', choices=['GET', 'POST', 'PATCH', 'DELETE'])
    request.add_argument('path')
    request.add_argument('--json', dest='payload', help='JSON text or @filename')
    request.add_argument('--query', action='append', default=[], metavar='NAME=VALUE')
    request.add_argument('--idempotency-key')
    request.add_argument('--output', help='Save a download to this path')
    login = commands.add_parser('login', help='Interactive token issuance for terminal users')
    login.add_argument('--username', required=True)
    login.add_argument('--expires-in', type=int, default=604800, help='Lifetime in seconds; 0 means permanent')
    login.add_argument('--name', default='', help='Name this credential for the token list')
    wait = commands.add_parser('wait')
    wait.add_argument('task_id', type=uuid.UUID)
    wait.add_argument('--timeout', type=float, default=300)
    wait.add_argument('--interval', type=float, default=5)
    args = parser.parse_args(argv)
    try:
        client = Client(args.url, os.environ.get('GPU_PLATFORM_TOKEN'))
        if args.command == 'login':
            password = os.environ.get('GPU_PLATFORM_PASSWORD')
            if not password:
                if not sys.stdin.isatty():
                    raise ClientError({'error': 'credentials_missing', 'message': 'Use the website API access page, or set GPU_PLATFORM_PASSWORD for login.'})
                password = getpass.getpass('Password: ')
            result = client.request('POST', '/api/v1/auth/token', {'username': args.username,
                'password': password, 'expires_in': args.expires_in, 'name': args.name})
        elif args.command == 'wait':
            if args.timeout < 0 or args.interval < 0.1:
                raise ClientError({'error': 'invalid_wait', 'message': 'Use timeout >= 0 and interval >= 0.1.'})
            deadline = time.monotonic()+args.timeout
            while True:
                result = client.request('GET', '/api/v1/tasks/'+str(args.task_id))
                task = result['data']
                if task.get('connection') or task['status'] in {'submitted', 'scheduled', 'succeeded', 'failed', 'canceled', 'expired', 'cleaned', 'rejected'}:
                    break
                if time.monotonic() >= deadline:
                    result['wait_timed_out'] = True
                    break
                time.sleep(min(args.interval, max(0, deadline-time.monotonic())))
        else:
            data = None
            if args.payload:
                text = Path(args.payload[1:]).read_text() if args.payload.startswith('@') else args.payload
                data = json.loads(text)
            query = []
            for entry in args.query:
                name, separator, value = entry.partition('=')
                if not separator:
                    raise ClientError({'error': 'invalid_query', 'message': 'Use --query NAME=VALUE'})
                query.append((name, value))
            result = client.request(args.method, args.path, data, args.idempotency_key, query, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ClientError as exc:
        print(json.dumps(exc.result, ensure_ascii=False), file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': 'invalid_input', 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
