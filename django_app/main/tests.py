import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from requests.exceptions import ConnectionError

from .models import Camera, CameraRecord
from .services import (
    _mediamtx_path_payload,
    mediamtx_add_path,
    mediamtx_delete_path,
    mediamtx_edit_path,
    mediamtx_request,
)
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


@override_settings(
    MEDIAMTX_API_URL='http://mediamtx:9997',
    MEDIAMTX_API_USERNAME='django-api',
    MEDIAMTX_API_PASSWORD='api-secret',
)
class MediaMTXAuthenticationTests(SimpleTestCase):
    @patch('main.services.requests.request')
    def test_control_api_uses_basic_auth(self, request):
        mediamtx_request('GET', '/v3/info')

        request.assert_called_once_with(
            'GET',
            'http://mediamtx:9997/v3/info',
            timeout=3,
            auth=('django-api', 'api-secret'),
        )

    @override_settings(MEDIAMTX_API_PASSWORD='')
    @patch('main.services.requests.request')
    def test_control_api_fails_closed_without_password(self, request):
        with self.assertRaises(RuntimeError):
            mediamtx_request('GET', '/v3/info')

        request.assert_not_called()

    @patch('main.services.requests.request')
    def test_control_api_keeps_explicit_timeout_and_overrides_supplied_auth(self, request):
        mediamtx_request(
            'PATCH',
            'v3/config/paths/patch/cam-one',
            timeout=9,
            auth=('untrusted', 'credentials'),
        )

        request.assert_called_once_with(
            'PATCH',
            'http://mediamtx:9997/v3/config/paths/patch/cam-one',
            timeout=9,
            auth=('django-api', 'api-secret'),
        )


@override_settings(MEDIAMTX_VIDEO_BITRATE_K=1500)
class MediaMTXServiceContractTests(SimpleTestCase):
    def setUp(self):
        self.camera = SimpleNamespace(
            name='cam one/entrance',
            rtsp_url='rtsp://camera-user:camera-pass@192.0.2.10:554/stream1',
        )

    def test_path_payload_uses_authenticated_internal_publisher_and_webhook_token(self):
        payload = _mediamtx_path_payload(self.camera)

        self.assertEqual(payload['source'], 'publisher')
        self.assertIn('-b:v 1500k', payload['runOnDemand'])
        self.assertIn('-maxrate 1500k', payload['runOnDemand'])
        self.assertIn('internal-publisher:$PUBLISH_PASSWORD@127.0.0.1', payload['runOnDemand'])
        self.assertIn('X-MediaMTX-Webhook-Token: $WEBHOOK_TOKEN', payload['runOnRecordSegmentComplete'])
        self.assertIn('X-MediaMTX-Webhook-Token: $WEBHOOK_TOKEN', payload['runOnUnread'])

    @patch('main.services.mediamtx_request')
    def test_add_path_url_encodes_camera_name(self, request):
        request.return_value = Mock(status_code=201, text='created')

        success, message = mediamtx_add_path(self.camera)

        self.assertTrue(success)
        self.assertEqual(message, 'created')
        self.assertEqual(request.call_args.args[:2], ('POST', '/v3/config/paths/add/cam%20one%2Fentrance'))
        self.assertEqual(request.call_args.kwargs['json']['source'], 'publisher')

    @patch('main.services.mediamtx_request')
    def test_delete_path_treats_missing_path_as_success(self, request):
        request.return_value = Mock(status_code=404, text='not found')

        success, _message = mediamtx_delete_path('cam one/entrance')

        self.assertTrue(success)
        request.assert_called_once_with('DELETE', '/v3/config/paths/delete/cam%20one%2Fentrance')

    @patch('main.services.mediamtx_add_path', return_value=(True, 'created'))
    @patch('main.services.mediamtx_request')
    def test_edit_path_creates_path_when_mediamtx_returns_404(self, request, add_path):
        request.return_value = Mock(status_code=404, text='not found')

        result = mediamtx_edit_path(self.camera)

        self.assertEqual(result, (True, 'created'))
        add_path.assert_called_once_with(self.camera)

    @patch('main.services.mediamtx_request', side_effect=ConnectionError('offline'))
    def test_add_path_reports_connection_failure(self, _request):
        success, message = mediamtx_add_path(self.camera)

        self.assertFalse(success)
        self.assertIn('offline', message)


@override_settings(CACHES=TEST_CACHES)
class CameraIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'operator',
            password='test-password',
            is_staff=True,
        )
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

    @override_settings(MEDIAMTX_WEBHOOK_TOKEN='webhook-secret')
    def test_record_webhook_rejects_missing_token(self):
        response = self.client.post(
            reverse('main:mediamtx_webhook'),
            {'path': self.camera.name, 'file': '/recordings/cam-one/record.mp4'},
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(MEDIAMTX_WEBHOOK_TOKEN='webhook-secret')
    def test_record_webhook_accepts_valid_token(self):
        response = self.client.post(
            reverse('main:mediamtx_webhook') + '?path=cam-one&file=/recordings/cam-one/record.mp4',
            HTTP_X_MEDIAMTX_WEBHOOK_TOKEN='webhook-secret',
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(CameraRecord.objects.filter(camera=self.camera, file_name='record.mp4').exists())


@override_settings(CACHES=TEST_CACHES)
class RoleAndProxyAuthenticationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('viewer', password='test-password')
        self.camera = Camera.objects.create(
            name='viewer-cam',
            camera_address='192.0.2.20',
            camera_port=554,
            camera_login='user',
            camera_password='password',
            camera_path='/stream1',
            onvif_port=80,
        )

    def test_management_page_rejects_non_staff_user(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('main:camera'))

        self.assertEqual(response.status_code, 403)

    @patch('main.views.check_ip', return_value=True)
    @patch('main.views.mediamtx_add_path')
    @patch('main.views.mediamtx_request', return_value=Mock(status_code=404))
    def test_mediamtx_path_sync_does_not_mutate_for_non_staff_user(
        self,
        _request,
        add_path,
        _check_ip,
    ):
        self.client.force_login(self.user)

        response = self.client.get(reverse('main:ensure_camera', args=[self.camera.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'error')
        add_path.assert_not_called()

    def test_stream_proxy_auth_rejects_anonymous_user(self):
        response = self.client.get(reverse('main:stream_proxy_auth'))

        self.assertEqual(response.status_code, 401)

    def test_stream_proxy_auth_accepts_authenticated_user(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('main:stream_proxy_auth'))

        self.assertEqual(response.status_code, 204)

    def test_anonymous_user_is_redirected_from_management(self):
        response = self.client.get(reverse('main:camera'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('account:login'), response.url)

    def test_non_staff_user_is_rejected_by_all_management_endpoints(self):
        record = CameraRecord.objects.create(
            camera=self.camera,
            file_name='record.mp4',
            file_path='/recordings/viewer-cam/record.mp4',
            start_time=timezone.now(),
        )
        target_user = get_user_model().objects.create_user('target', password='test-password')
        self.client.force_login(self.user)

        requests = [
            ('get', reverse('main:camera'), None),
            ('get', reverse('main:config'), None),
            ('get', reverse('main:get_bitrate', args=[self.camera.id]), None),
            ('get', reverse('main:get_all_cameras_status'), None),
            ('post', reverse('main:create_camera'), {}),
            ('post', reverse('main:edit_camera', args=[self.camera.id]), {}),
            ('post', reverse('main:delete_camera', args=[self.camera.id]), {}),
            ('post', reverse('main:toggle_record', args=[self.camera.id]), {}),
            ('post', reverse('main:delete_record', args=[record.id]), {}),
            ('post', reverse('main:set_camera_resolution', args=[self.camera.id]), {}),
            ('post', reverse('main:set_camera_fps', args=[self.camera.id]), {}),
            ('get', reverse('account:index'), None),
            ('post', reverse('account:create_user'), {}),
            ('post', reverse('account:edit_user', args=[target_user.id]), {}),
            ('post', reverse('account:delete_user', args=[target_user.id]), {}),
        ]

        for method, url, data in requests:
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, data=data)
                self.assertEqual(response.status_code, 403)

        self.assertTrue(Camera.objects.filter(pk=self.camera.id).exists())
        self.assertTrue(CameraRecord.objects.filter(pk=record.id).exists())
        self.assertTrue(get_user_model().objects.filter(pk=target_user.id).exists())


@override_settings(MEDIAMTX_WEBHOOK_TOKEN='webhook-secret')
class MediaMTXWebhookTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.recordings_root = Path(self.directory.name)
        self.settings_override = override_settings(RECORDINGS_ROOT=self.recordings_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.camera = Camera.objects.create(
            name='webhook-cam',
            camera_address='192.0.2.30',
            camera_port=554,
            camera_login='user',
            camera_password='password',
            camera_path='/stream1',
            onvif_port=80,
            is_recording=True,
        )
        self.headers = {'HTTP_X_MEDIAMTX_WEBHOOK_TOKEN': 'webhook-secret'}

    def record_webhook_url(self, file_name='2026-09-20_12-30-45.mp4'):
        return (
            reverse('main:mediamtx_webhook')
            + f'?path={self.camera.name}&file=/recordings/{self.camera.name}/{file_name}'
        )

    def stop_webhook_url(self):
        return reverse('main:mediamtx_record_stop_webhook') + f'?path={self.camera.name}'

    def test_webhook_rejects_get_requests(self):
        response = self.client.get(self.record_webhook_url(), **self.headers)

        self.assertEqual(response.status_code, 405)

    def test_webhook_rejects_invalid_token(self):
        response = self.client.post(
            self.record_webhook_url(),
            HTTP_X_MEDIAMTX_WEBHOOK_TOKEN='wrong-secret',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(CameraRecord.objects.exists())

    @override_settings(MEDIAMTX_WEBHOOK_TOKEN='')
    def test_webhook_fails_closed_when_token_is_not_configured(self):
        response = self.client.post(self.record_webhook_url(), **self.headers)

        self.assertEqual(response.status_code, 503)
        self.assertFalse(CameraRecord.objects.exists())

    def test_webhook_requires_path_and_file(self):
        response = self.client.post(reverse('main:mediamtx_webhook'), **self.headers)

        self.assertEqual(response.status_code, 400)

    def test_webhook_rejects_unknown_camera(self):
        url = reverse('main:mediamtx_webhook') + '?path=missing&file=/recordings/missing/file.mp4'

        response = self.client.post(url, **self.headers)

        self.assertEqual(response.status_code, 404)

    def test_webhook_creates_record_with_file_metadata_and_timestamp(self):
        camera_directory = self.recordings_root / self.camera.name
        camera_directory.mkdir()
        video = camera_directory / '2026-09-20_12-30-45.mp4'
        video.write_bytes(b'video-data')

        response = self.client.post(self.record_webhook_url(video.name), **self.headers)

        self.assertEqual(response.status_code, 201)
        record = CameraRecord.objects.get()
        self.assertEqual(record.file_name, video.name)
        self.assertEqual(record.file_size_bytes, len(b'video-data'))
        self.assertEqual(record.duration_seconds, 3600)
        self.assertEqual(
            timezone.localtime(record.start_time).replace(tzinfo=None),
            datetime(2026, 9, 20, 12, 30, 45),
        )

    def test_repeated_webhook_updates_existing_record_without_duplicate(self):
        camera_directory = self.recordings_root / self.camera.name
        camera_directory.mkdir()
        video = camera_directory / '2026-09-20_12-30-45.mp4'
        video.write_bytes(b'one')
        url = self.record_webhook_url(video.name)

        self.client.post(url, **self.headers)
        video.write_bytes(b'updated-video')
        response = self.client.post(url, **self.headers)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(CameraRecord.objects.count(), 1)
        self.assertEqual(CameraRecord.objects.get().file_size_bytes, len(b'updated-video'))

    @patch('main.views.mediamtx_request', return_value=Mock(status_code=204, text=''))
    def test_stop_webhook_updates_mediamtx_and_database(self, request):
        response = self.client.post(self.stop_webhook_url(), **self.headers)

        self.assertEqual(response.status_code, 200)
        request.assert_called_once_with(
            'PATCH',
            f'/v3/config/paths/patch/{self.camera.name}',
            json={'record': False},
            timeout=5,
        )
        self.camera.refresh_from_db()
        self.assertFalse(self.camera.is_recording)

    @patch('main.views.mediamtx_request', return_value=Mock(status_code=500, text='error'))
    def test_stop_webhook_keeps_database_state_when_mediamtx_rejects_request(self, _request):
        response = self.client.post(self.stop_webhook_url(), **self.headers)

        self.assertEqual(response.status_code, 502)
        self.camera.refresh_from_db()
        self.assertTrue(self.camera.is_recording)

    @patch('main.views.mediamtx_request', side_effect=ConnectionError('offline'))
    def test_stop_webhook_returns_503_when_mediamtx_is_offline(self, _request):
        response = self.client.post(self.stop_webhook_url(), **self.headers)

        self.assertEqual(response.status_code, 503)
        self.camera.refresh_from_db()
        self.assertTrue(self.camera.is_recording)


class ArchiveAccessTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.recordings_root = Path(self.directory.name)
        self.settings_override = override_settings(RECORDINGS_ROOT=self.recordings_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.viewer = get_user_model().objects.create_user('archive-viewer', password='test-password')
        self.staff = get_user_model().objects.create_user(
            'archive-admin',
            password='test-password',
            is_staff=True,
        )
        self.camera = Camera.objects.create(
            name='archive-cam',
            camera_address='192.0.2.40',
            camera_port=554,
            camera_path='/stream1',
            onvif_port=80,
        )

    def create_record(self, file_name='record.mp4', start_time=None, content=b'video'):
        camera_directory = self.recordings_root / self.camera.name
        camera_directory.mkdir(exist_ok=True)
        video = camera_directory / file_name
        video.write_bytes(content)
        return CameraRecord.objects.create(
            camera=self.camera,
            file_name=file_name,
            file_path=str(video),
            file_size_bytes=len(content),
            start_time=start_time or timezone.now(),
            duration_seconds=60,
        )

    def test_anonymous_user_cannot_open_or_download_archive(self):
        record = self.create_record()

        for url in (
            reverse('main:camera_records'),
            reverse('main:download_record', args=[record.id]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('account:login'), response.url)

    def test_archive_is_sorted_newest_first(self):
        older = self.create_record('older.mp4', timezone.now() - timedelta(hours=1))
        newer = self.create_record('newer.mp4', timezone.now())
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('main:camera_records'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['records']), [newer, older])

    def test_viewer_sees_download_but_not_delete_action(self):
        record = self.create_record()
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('main:camera_records'))

        self.assertContains(response, reverse('main:download_record', args=[record.id]))
        self.assertNotContains(response, reverse('main:delete_record', args=[record.id]))

    def test_viewer_cannot_delete_record_or_file(self):
        record = self.create_record()
        file_path = Path(record.file_path)
        self.client.force_login(self.viewer)

        response = self.client.post(reverse('main:delete_record', args=[record.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(file_path.exists())
        self.assertTrue(CameraRecord.objects.filter(pk=record.id).exists())

    def test_staff_delete_removes_record_and_file(self):
        record = self.create_record()
        file_path = Path(record.file_path)
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('main:delete_record', args=[record.id]),
            HTTP_REFERER=reverse('main:camera_records'),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(file_path.exists())
        self.assertFalse(CameraRecord.objects.filter(pk=record.id).exists())

    def test_staff_delete_uses_archive_as_safe_fallback_redirect(self):
        record = self.create_record()
        self.client.force_login(self.staff)

        response = self.client.post(reverse('main:delete_record', args=[record.id]))

        self.assertRedirects(response, reverse('main:camera_records'))

    def test_delete_rejects_record_path_outside_recordings_root(self):
        outside_directory = tempfile.TemporaryDirectory()
        self.addCleanup(outside_directory.cleanup)
        outside_file = Path(outside_directory.name) / 'outside.mp4'
        outside_file.write_bytes(b'do-not-delete')
        record = CameraRecord.objects.create(
            camera=self.camera,
            file_name=outside_file.name,
            file_path=str(outside_file),
            start_time=timezone.now(),
        )
        self.client.force_login(self.staff)

        response = self.client.post(reverse('main:delete_record', args=[record.id]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(outside_file.exists())
        self.assertTrue(CameraRecord.objects.filter(pk=record.id).exists())

    def test_download_rejects_record_path_outside_recordings_root(self):
        outside_directory = tempfile.TemporaryDirectory()
        self.addCleanup(outside_directory.cleanup)
        outside_file = Path(outside_directory.name) / 'outside.mp4'
        outside_file.write_bytes(b'video')
        record = CameraRecord.objects.create(
            camera=self.camera,
            file_name=outside_file.name,
            file_path=str(outside_file),
            start_time=timezone.now(),
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('main:download_record', args=[record.id]))

        self.assertEqual(response.status_code, 404)

    def test_download_returns_404_when_file_is_missing(self):
        record = CameraRecord.objects.create(
            camera=self.camera,
            file_name='missing.mp4',
            file_path=str(self.recordings_root / self.camera.name / 'missing.mp4'),
            start_time=timezone.now(),
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('main:download_record', args=[record.id]))

        self.assertEqual(response.status_code, 404)

    @override_settings(USE_X_ACCEL_REDIRECT=False)
    def test_download_can_stream_file_without_nginx_acceleration(self):
        record = self.create_record(content=b'archive-content')
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('main:download_record', args=[record.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'archive-content')
        self.assertIn('attachment', response['Content-Disposition'])


class MediaMTXViewIntegrationTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            'mediamtx-admin',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.camera = Camera.objects.create(
            name='integration-cam',
            camera_address='192.0.2.50',
            camera_port=554,
            camera_path='/stream1',
            onvif_port=80,
        )

    @patch('main.views.mediamtx_request', return_value=Mock(status_code=204, text=''))
    def test_toggle_record_updates_database_only_after_mediamtx_success(self, request):
        response = self.client.post(reverse('main:toggle_record', args=[self.camera.id]))

        self.assertEqual(response.status_code, 302)
        request.assert_called_once_with(
            'PATCH',
            f'/v3/config/paths/patch/{self.camera.name}',
            json={'record': True},
            timeout=5,
        )
        self.camera.refresh_from_db()
        self.assertTrue(self.camera.is_recording)

    @patch('main.views.mediamtx_request', return_value=Mock(status_code=500, text='error'))
    def test_toggle_record_keeps_database_state_after_mediamtx_error(self, _request):
        response = self.client.post(reverse('main:toggle_record', args=[self.camera.id]))

        self.assertEqual(response.status_code, 302)
        self.camera.refresh_from_db()
        self.assertFalse(self.camera.is_recording)

    @patch('main.views.check_ip', return_value=True)
    @patch('main.views.mediamtx_add_path', return_value=(True, 'created'))
    @patch('main.views.mediamtx_request', return_value=Mock(status_code=404, text='not found'))
    def test_staff_can_restore_missing_mediamtx_path(self, request, add_path, _check_ip):
        response = self.client.get(reverse('main:ensure_camera', args=[self.camera.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        request.assert_called_once_with(
            'GET',
            f'/v3/config/paths/get/{self.camera.name}',
            timeout=5,
        )
        add_path.assert_called_once_with(self.camera)

    @patch('main.views.check_ip', return_value=True)
    @patch('main.views.mediamtx_add_path')
    @patch('main.views.mediamtx_request', return_value=Mock(status_code=200, text='ok'))
    def test_existing_mediamtx_path_is_not_recreated(self, _request, add_path, _check_ip):
        response = self.client.get(reverse('main:ensure_camera', args=[self.camera.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        add_path.assert_not_called()
