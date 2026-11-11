import importlib.util
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('host_metrics', ROOT / 'deploy/host-metrics.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


class HostMetricsTests(TestCase):
    def test_cpu_excludes_guest_double_counting_and_treats_iowait_as_idle(self):
        previous = metrics.cpu_counter('cpu 10 0 10 70 10 0 0 0 4 0\n')
        current = metrics.cpu_counter('cpu 30 0 20 120 30 0 0 0 14 0\n')
        self.assertEqual(metrics.cpu_percent(previous, current), 30)
        self.assertIsNone(metrics.cpu_percent(current, current))
        self.assertIsNone(metrics.cpu_percent(current, previous))

    def test_memory_uses_available_memory_including_reclaimable_cache(self):
        result = metrics.memory_metrics('MemTotal: 8388608 kB\nMemFree: 1048576 kB\nMemAvailable: 6291456 kB\n')
        self.assertEqual(result, {'total_gib': 8, 'used_gib': 2, 'available_gib': 6, 'percent': 25})

    def test_gpu_readings_preserve_zero_and_unsupported_sensor_values(self):
        text = '0, GPU-a, NVIDIA GeForce RTX 3080, 0, 1024, 10240, 45, 67.5, 320, [N/A]\n'
        gpu = metrics.gpu_metrics(text)[0]
        self.assertEqual((gpu['utilization_percent'], gpu['memory_percent'], gpu['temperature_c']), (0, 10, 45))
        self.assertEqual(gpu['power_w'], 67.5)
        self.assertIsNone(gpu['fan_percent'])
        self.assertIsNone(metrics.number('NaN'))
        self.assertIsNone(metrics.number('-1'))

    def test_cpu_temperature_ignores_nvme_and_reports_hottest_cpu_sensor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, driver, value in [('hwmon0', 'nvme', '99000'), ('hwmon1', 'coretemp', '47000'), ('hwmon2', 'k10temp', '53000')]:
                device = root / name
                device.mkdir()
                (device / 'name').write_text(driver)
                (device / 'temp1_input').write_text(value)
            result = metrics.cpu_temperatures(root, root / 'thermal')
            self.assertEqual(result['temperature_c'], 53)
            self.assertEqual(len(result['temperature_sensors']), 2)

    def test_cpu_temperature_uses_cpu_thermal_zone_and_missing_is_null(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(metrics.cpu_temperatures(root, root)['temperature_c'])
            zone = root / 'thermal_zone0'
            zone.mkdir()
            (zone / 'type').write_text('x86_pkg_temp')
            (zone / 'temp').write_text('61000')
            self.assertEqual(metrics.cpu_temperatures(root, root)['temperature_c'], 61)

    def test_installer_only_restarts_the_metrics_service(self):
        spec = importlib.util.spec_from_file_location('metrics_service', ROOT / 'deploy/metrics_service.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def redirect(path):
                return root / path.lstrip('/')
            (root / 'etc/systemd/system').mkdir(parents=True)
            with patch.object(installer, 'Path', side_effect=redirect), \
                    patch.object(installer.shutil, 'copyfile', side_effect=lambda source, target: target.write_text('collector')), \
                    patch.object(installer.subprocess, 'run') as run:
                installer.install_metrics('/project', '/etc/agent.yaml')
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands, [['systemctl', 'daemon-reload'], ['systemctl', 'enable', 'gpu-platform-metrics.service'],
                                        ['systemctl', 'restart', 'gpu-platform-metrics.service']])
