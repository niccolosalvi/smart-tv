"""Read-only host telemetry. No TV commands and no privileged operations."""
from datetime import datetime, timezone
from pathlib import Path
import platform
import subprocess
import threading
import time
import uuid


class Health:
    def __init__(self):
        self.started = datetime.now(timezone.utc).isoformat()
        self.clock = time.monotonic()
        self.session = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.cached_at = float('-inf')
        self.services = {}

    def _unit(self, name):
        try:
            result = subprocess.run(
                ['systemctl', 'show', name, '--no-pager',
                 '--property=LoadState,ActiveState,UnitFileState,NextElapseUSecRealtime'],
                capture_output=True, text=True, timeout=2, check=True)
            values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            return {'available': values.get('LoadState') == 'loaded',
                    'active': values.get('ActiveState'),
                    'enabled': values.get('UnitFileState') == 'enabled',
                    'next_run': values.get('NextElapseUSecRealtime') or None}
        except (OSError, subprocess.SubprocessError):
            return {'available': False, 'active': None, 'enabled': None, 'next_run': None}

    def snapshot(self, command_busy=False):
        with self.lock:
            if time.monotonic() - self.cached_at > 30:
                self.services = {key: self._unit(unit) for key, unit in (
                    ('service', 'smart-tv.service'), ('refresh_timer', 'smart-tv-refresh.timer'))}
                self.cached_at = time.monotonic()
            services = self.services.copy()
        model = None
        try:
            model = Path('/proc/device-tree/model').read_text().rstrip('\x00\n')
        except OSError:
            pass
        memory = {}
        try:
            fields = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
            memory = {key: round(int(fields[source].split()[0]) / 1024, 1)
                      for key, source in [('total_mb', 'MemTotal'), ('available_mb', 'MemAvailable')]}
        except (OSError, KeyError, ValueError):
            pass
        host_uptime = None
        process_mb = None
        try:
            host_uptime = int(float(Path('/proc/uptime').read_text().split()[0]))
            for line in Path('/proc/self/status').read_text().splitlines():
                if line.startswith('VmRSS:'):
                    process_mb = round(int(line.split()[1]) / 1024, 1)
        except (OSError, ValueError):
            pass
        return {'status': 'ok', 'session': self.session, 'checked_at': datetime.now(timezone.utc).isoformat(),
                'hostname': platform.node(), 'is_raspberry': bool(model and 'Raspberry' in model),
                'model': model, 'started_at': self.started,
                'server_uptime_seconds': int(time.monotonic() - self.clock),
                'host_uptime_seconds': host_uptime, 'process_memory_mb': process_mb,
                'memory': memory, 'command_busy': command_busy, **services}
