from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TransactionTestCase

from ... import settings as monitoring_settings
from ...device.tests import TestDeviceMonitoringMixin
from ...monitoring.models import Graph, Metric, Threshold
from .. import settings
from ..classes import Ping
from ..exceptions import OperationalError
from ..models import Check


class TestPing(TestDeviceMonitoringMixin, TransactionTestCase):
    _PING = settings.CHECK_CLASSES[0][0]
    _RESULT_KEYS = ['reachable', 'loss', 'rtt_min', 'rtt_avg', 'rtt_max']
    _RTT_KEYS = _RESULT_KEYS[-3:]
    _UNRECOGNIZED_OUTPUT = (
        '',
        bytes("fping: option requires an argument -- 'z'", encoding='utf8'),
    )
    _FPING_OUTPUT = (
        '',
        bytes(
            '10.40.0.1 : xmt/rcv/%loss = 5/5/0%, ' 'min/avg/max = 0.04/0.08/0.15',
            'utf8',
        ),
    )

    def test_check_ping_no_params(self):
        device = self._create_device(organization=self._create_org())
        # will ping localhost
        device.management_ip = '127.0.0.1'
        check = Check(
            name='Ping check', check=self._PING, content_object=device, params={}
        )
        result = check.perform_check(store=False)
        for key in self._RESULT_KEYS:
            self.assertIn(key, result)
        self.assertEqual(result['reachable'], 1)
        self.assertEqual(result['loss'], 0.0)
        for key in self._RTT_KEYS:
            self.assertTrue(result[key] < 1)

    def test_check_ping_params(self):
        device = self._create_device(organization=self._create_org())
        # will ping localhost
        device.management_ip = '127.0.0.1'
        check = Check(
            name='Ping check',
            check=self._PING,
            content_object=device,
            params={'count': 2, 'interval': 10, 'bytes': 10, 'timeout': 50},
        )
        result = check.perform_check(store=False)
        for key in self._RESULT_KEYS:
            self.assertIn(key, result)
        self.assertEqual(result['reachable'], 1)
        self.assertEqual(result['loss'], 0.0)
        for key in self._RTT_KEYS:
            self.assertTrue(result[key] < 1)

    def test_check_ping_unreachable(self):
        device = self._create_device(organization=self._create_org())
        # will hopefully ping an unexisting private address
        device.management_ip = '192.168.255.255'
        check = Check(
            name='Ping check',
            check=self._PING,
            content_object=device,
            params={'timeout': 50, 'count': 3},
        )
        result = check.perform_check(store=False)
        for key in self._RESULT_KEYS[0:2]:
            self.assertIn(key, result)
        self.assertFalse(result['reachable'], 0)
        self.assertEqual(result['loss'], 100.0)

    @patch.object(Ping, '_command', return_value=_UNRECOGNIZED_OUTPUT)
    def test_operational_error(self, _command):
        device = self._create_device(organization=self._create_org())
        device.management_ip = '127.0.0.1'
        check = Check(
            name='Ping check',
            check=self._PING,
            content_object=device,
            params={'timeout': 50, 'count': 3},
        )
        try:
            check.perform_check(store=False)
        except OperationalError as e:
            self.assertIn('Unrecognized fping output:', str(e))
        else:
            self.fail('OperationalError not raised')

    def _check_no_ip_case(self, status, management_ip_only=False):
        device = self._create_device(
            organization=self._create_org(), last_ip='127.0.0.1'
        )
        device.monitoring.update_status(status)
        check = Check(
            name='Ping check', check=self._PING, content_object=device, params={}
        )
        result = check.perform_check(store=True)
        if not management_ip_only:
            if status != 'unknown':
                expected_result = {'reachable': 0, 'loss': 100}
                expected_status = 'critical'
                expected_metrics_count = 1
            else:
                expected_result = None
                expected_status = status
                expected_metrics_count = 0
            self.assertEqual(result, expected_result)
        else:
            expected_status = 'ok'
            expected_metrics_count = 1
        device.monitoring.refresh_from_db()
        self.assertEqual(device.monitoring.status, expected_status)
        self.assertEqual(Metric.objects.count(), expected_metrics_count)

    def test_device_without_ip_unknown_status(self):
        self._check_no_ip_case('unknown')

    def test_device_without_ip_ok_status(self):
        self._check_no_ip_case('ok')

    def test_device_without_ip_problem_status(self):
        self._check_no_ip_case('problem')

    def test_device_without_ip_critical_status(self):
        self._check_no_ip_case('critical')

    @patch('openwisp_monitoring.device.settings.MANAGEMENT_IP_ONLY', False)
    def test_device_with_last_ip_unknown_status(self):
        self._check_no_ip_case('unknown', management_ip_only=True)

    def test_content_object_none(self):
        check = Check(name='Ping check', check=self._PING, params={})
        try:
            check.check_instance.validate()
        except ValidationError as e:
            self.assertIn('device', str(e))
        else:
            self.fail('ValidationError not raised')

    def test_content_object_not_device(self):
        check = Check(
            name='Ping check',
            check=self._PING,
            content_object=self._create_user(),
            params={},
        )
        try:
            check.check_instance.validate()
        except ValidationError as e:
            self.assertIn('device', str(e))
        else:
            self.fail('ValidationError not raised')

    def test_schema_violation(self):
        device = self._create_device(organization=self._create_org())
        device.management_ip = '127.0.0.1'
        invalid_params = [
            {'count': 1},
            {'interval': 9},
            {'bytes': 0},
            {'timeout': 2},
            {'count': 'ciao'},
            {'wrong': True},
            {'bytes': 999999, 'count': 99},
        ]
        for params in invalid_params:
            check = Check(
                name='Ping check',
                check=self._PING,
                content_object=device,
                params=params,
            )
            try:
                check.check_instance.validate()
            except ValidationError as e:
                self.assertIn('Invalid param', str(e))
            else:
                self.fail('ValidationError not raised')

    @patch.object(Ping, '_command', return_value=_FPING_OUTPUT)
    def test_store_result(self, mocked_method):
        self.assertEqual(Check.objects.count(), 0)
        device = self._create_device(organization=self._create_org())
        device.management_ip = '10.40.0.1'
        device.save()
        # check created automatically by autoping
        self.assertEqual(Check.objects.count(), 1)
        self.assertEqual(Metric.objects.count(), 0)
        self.assertEqual(Graph.objects.count(), 0)
        self.assertEqual(Threshold.objects.count(), 0)
        check = Check.objects.first()
        result = check.perform_check()
        self.assertEqual(Metric.objects.count(), 1)
        self.assertEqual(Graph.objects.count(), 3)
        self.assertEqual(Threshold.objects.count(), 1)
        m = Metric.objects.first()
        self.assertEqual(m.content_object, device)
        self.assertEqual(m.key, 'ping')
        points = m.read(limit=None, extra_fields=list(result.keys()))
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]['reachable'], result['reachable'])
        self.assertEqual(points[0]['loss'], result['loss'])
        self.assertEqual(points[0]['rtt_min'], result['rtt_min'])
        self.assertEqual(points[0]['rtt_avg'], result['rtt_avg'])
        self.assertEqual(points[0]['rtt_max'], result['rtt_max'])

    @patch.object(Ping, '_command', return_value=_FPING_OUTPUT)
    @patch.object(monitoring_settings, 'AUTO_GRAPHS', return_value=[])
    def test_auto_graph_disabled(self, *args):
        device = self._create_device(organization=self._create_org())
        device.last_ip = '127.0.0.1'
        device.save()
        check = Check.objects.first()
        self.assertEqual(Graph.objects.count(), 0)
        check.perform_check()
        self.assertEqual(Graph.objects.count(), 0)
