"""Opt-in tests against a disposable MediaMTX, never a production server."""
import os
import time
import uuid
from types import SimpleNamespace
from unittest import skipUnless

import requests
from django.conf import settings
from django.test import SimpleTestCase

from .services import mediamtx_add_path, mediamtx_edit_path, mediamtx_request


@skipUnless(os.environ.get('RUN_MEDIAMTX_LIVE_TESTS') == '1', 'Disposable MediaMTX required')
class MediaMTXLiveTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        deadline = time.monotonic() + 40
        while True:
            try:
                response = mediamtx_request('GET', '/v3/config/global/get')
                if response.status_code == 200:
                    break
            except requests.RequestException:
                pass
            if time.monotonic() >= deadline:
                raise AssertionError('MediaMTX did not start with the project configuration')
            time.sleep(0.5)

    def test_api_rejects_anonymous_and_invalid_password(self):
        url = settings.MEDIAMTX_API_URL + '/v3/config/global/get'
        for auth in (None, ('django-api', 'wrong-password')):
            with self.subTest(authenticated=auth is not None):
                response = requests.get(url, auth=auth, timeout=5)
                self.assertIn(response.status_code, (401, 403))

    def test_dynamic_path_inherits_archive_settings_and_preserves_recording(self):
        camera = SimpleNamespace(
            name='test-' + uuid.uuid4().hex,
            rtsp_url='rtsp://192.0.2.1:554/unused',
            is_recording=True,
        )
        self.addCleanup(mediamtx_request, 'DELETE', '/v3/config/paths/delete/' + camera.name)
        success, message = mediamtx_add_path(camera)
        self.assertTrue(success, message)
        # Seed the previous release's disconnect hook, then apply the new payload.
        response = mediamtx_request('PATCH', '/v3/config/paths/patch/' + camera.name,
                                    json={'runOnUnread': 'echo legacy'})
        response.raise_for_status()
        success, message = mediamtx_edit_path(camera)
        self.assertTrue(success, message)
        response = mediamtx_request('GET', '/v3/config/paths/get/' + camera.name)
        response.raise_for_status()
        config = response.json()
        self.assertTrue(config['record'])
        self.assertEqual(config['runOnUnread'], '')
        self.assertTrue(config['recordPath'].startswith('/recordings/'))
        self.assertIn('$MTX_SEGMENT_PATH', config['runOnRecordSegmentCreate'])
        self.assertNotIn('$MTX_FILE_PATH', config['runOnRecordSegmentCreate'])
        camera.is_recording = False
        self.assertTrue(mediamtx_edit_path(camera)[0])
        response = mediamtx_request('GET', '/v3/config/paths/get/' + camera.name)
        self.assertFalse(response.json()['record'])
