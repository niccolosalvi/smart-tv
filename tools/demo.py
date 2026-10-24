"""Run a local demonstration with simulated TV, guide and host telemetry."""
import argparse
import os
import tempfile
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='smart-tv-demo-') as runtime:
        # Isolate all demo state before importing the application.
        os.environ['LOCALAPPDATA'] = runtime
        os.environ['TV_HOST'] = '127.0.0.1'
        import server
        from epg import FAVORITES

        class DemoRemote:
            tv = SimpleNamespace(is_alive=lambda: False)

            def send(self, *args, **kwargs):
                return {'status': 'demo', 'note': 'Simulated; no TV command sent'}

            switch_to_pc = send
            switch_to_tv = send

            def launch_app(self, app):
                return {**self.send(), 'app_name': server.APPS[app]['name']}

        class DemoGuide:
            def snapshot(self):
                now = time.time()
                current = ['Documentario: mari e coste', 'Una giornata in citta',
                           'Viaggi e sapori', 'Pianeta natura',
                           'Avventure nel futuro', 'Officina creativa']
                return {'source': 'Guida dimostrativa', 'source_url': '',
                        'updated_at': now - 3600, 'downloaded_at': now - 3600,
                        'source_updated_at': now - 7200, 'server_time': now,
                        'stale': False, 'error': None, 'auto_refresh': True,
                        'next_refresh_at': now + 23*3600,
                        'channels': [
                            {'number': number, 'name': name,
                             'current': {'start': now-1200, 'end': now+1800, 'title': title},
                             'next': {'start': now+1800, 'end': now+5400, 'title': 'Il prossimo episodio'}}
                            for (number, name, _), title in zip(FAVORITES, current)]}

        class DemoHealth:
            def snapshot(self, command_busy=False):
                return {'status': 'ok', 'session': 'public-demo',
                        'checked_at': datetime.now(timezone.utc).isoformat(),
                        'hostname': 'demo-device', 'is_raspberry': True,
                        'model': 'Raspberry Pi · Demo',
                        'host_uptime_seconds': 172800, 'server_uptime_seconds': 3600,
                        'process_memory_mb': 32, 'memory': {'total_mb': 1024, 'available_mb': 768},
                        'command_busy': command_busy,
                        'service': {'available': True, 'active': 'active', 'enabled': True},
                        'refresh_timer': {'available': True, 'active': 'active',
                                          'enabled': True, 'next_run': '11:00 Europe/Rome'}}

        server.remote = DemoRemote()
        server.guide = DemoGuide()
        server.health = DemoHealth()
        server.wake = lambda: {'status': 'demo'}
        server.power_off = lambda remote: {'status': 'demo'}
        server.view.select('tv')
        http = ThreadingHTTPServer(('127.0.0.1', args.port), server.Handler)
        http.daemon_threads = True
        print(f'Demo (simulated devices): http://127.0.0.1:{args.port}', flush=True)
        try:
            http.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            http.server_close()


if __name__ == '__main__':
    main()
