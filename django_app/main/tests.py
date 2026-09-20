import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Camera, CameraRecord
from .views import _calculate_bitrate_mbps, _extract_bytes_received


TEST_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'mediaserver-tests',
    }
}


@override_settings(CACHES=TEST_CACHES)
class BitrateTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_extracts_counter_from_source_state(self):
        self.assertEqual(
            _extract_bytes_received({'sourceState': {'bytesReceived': '1250'}}),
            1250,
        )

    @patch('main.views.time.monotonic', side_effect=[10.0, 12.0])
    def test_calculates_bitrate_from_counter_delta(self, _monotonic):
        self.assertIsNone(_calculate_bitrate_mbps(7, 1_000_000, 'test'))
        self.assertEqual(_calculate_bitrate_mbps(7, 2_000_000, 'test'), 4.0)


@override_settings(CACHES=TEST_CACHES)
class CameraIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('operator', password='test-password')
        self.client.force_login(self.user)
        self.camera = Camera.objects.create(
            name='cam-one',
            camera_address='192.0.2.10',
            camera_port=554,
            camera_login='user',
            camera_password='password',
            camera_path='/stream1',
            onvif_port=80,
        )

    @patch('main.views.mediamtx_delete_path', return_value=(True, ''))
    @patch('main.views.mediamtx_add_path', return_value=(True, ''))
    def test_camera_rename_creates_new_path_then_deletes_old(self, add_path, delete_path):
        response = self.client.post(
            reverse('main:edit_camera', args=[self.camera.id]),
            {
                'camera_name': 'cam-two',
                'camera_description': '',
                'camera_address': '192.0.2.10',
                'camera_port': '554',
                'camera_login': 'user',
                'camera_password': 'password',
                'camera_path': '/stream1',
                'onvif_port': '80',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.camera.refresh_from_db()
        self.assertEqual(self.camera.name, 'cam-two')
        add_path.assert_called_once()
        delete_path.assert_called_once_with('cam-one')

    @patch.object(Camera, 'set_resolution', return_value={'error': 'camera offline'})
    def test_resolution_endpoint_propagates_onvif_error(self, _set_resolution):
        response = self.client.post(
            reverse('main:set_camera_resolution', args=[self.camera.id]),
            data=json.dumps({'resolution': '1920x1080'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['status'], 'error')

    def test_download_uses_nginx_internal_redirect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            camera_directory = root / self.camera.name
            camera_directory.mkdir()
            video = camera_directory / 'record.mp4'
            video.write_bytes(b'video')
            record = CameraRecord.objects.create(
                camera=self.camera,
                file_name=video.name,
                file_path=str(video),
                file_size_bytes=video.stat().st_size,
                start_time=timezone.now(),
                duration_seconds=int(timedelta(seconds=1).total_seconds()),
            )

            with override_settings(RECORDINGS_ROOT=root):
                response = self.client.get(reverse('main:download_record', args=[record.id]))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response['X-Accel-Redirect'],
                f'/protected_recordings/{self.camera.name}/{video.name}',
            )
