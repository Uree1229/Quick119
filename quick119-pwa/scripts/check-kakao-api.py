"""Opt-in live probe with public sample coordinates and a disposable database."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Call both Kakao APIs once')
    args = parser.parse_args()
    if not args.live:
        parser.error('Explicit --live is required')
    # The HTTPS launcher supplies the opt-in flags. Never open the patient's DB.
    if (os.getenv('QUICK119_ENV') != 'development'
            or os.getenv('QUICK119_EXTERNAL_API') != '1'
            or os.getenv('QUICK119_LOAD_ENV') != '1'):
        print(json.dumps({'code': 'development_api_opt_in_required'}))
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix='quick119-kakao-probe-') as folder:
        os.environ['QUICK119_DB_PATH'] = str(Path(folder) / 'synthetic.db')
        import main as application
        from settings import kakao_rest_api_key
        from fastapi.testclient import TestClient

        # Public coordinates around Pangyo. No patient names, records or real GPS.
        origin = {'lat': 37.402464820205246, 'lon': 127.10764191124568}
        points = [
            {'key': 'sample-1', 'lat': 37.39419693653072, 'lon': 127.11056336672839},
            {'key': 'sample-2', 'lat': 37.394912, 'lon': 127.111202},
            {'key': 'sample-3', 'lat': 37.4001, 'lon': 127.1085},
            {'key': 'sample-4', 'lat': 37.401, 'lon': 127.109},
            {'key': 'sample-5', 'lat': 37.399, 'lon': 127.110},
        ]
        with TestClient(application.app) as client:
            loaded = bool(kakao_rest_api_key())
            print(json.dumps({'key_loaded': loaded, 'database': 'temporary_synthetic_only'}))
            if not loaded:
                return 1
            success = True
            for path, body in [
                ('/api/routes/car', {'origin': origin, 'destination': {
                    'lat': points[0]['lat'], 'lon': points[0]['lon']}}),
                ('/api/routes/car/compare', {'origin': origin, 'destinations': points}),
            ]:
                response = client.post(path, json=body)
                data = response.json()
                # Strict allowlist: no raw bodies, request headers, URLs or exception strings.
                report = {'endpoint': path, 'http_status': response.status_code,
                          'code': data.get('code'), 'upstream_http_status': data.get('upstream_http_status')}
                if 'routes' in data:
                    report['routes'] = [{k: route[k] for k in
                        ('key', 'code', 'result_code', 'distance_m', 'duration_sec') if k in route}
                        for route in data['routes']]
                    success = success and len(data['routes']) == 5 and all(r['code'] == 'ok' for r in data['routes'])
                else:
                    report.update({k: data[k] for k in ('result_code', 'distance_m', 'duration_sec') if k in data})
                    report['geometry_points'] = len(data.get('coordinates', []))
                    success = success and bool(data.get('coordinates'))
                print(json.dumps(report, ensure_ascii=True))
                success = success and response.status_code == 200 and data.get('code') == 'ok'
            return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
