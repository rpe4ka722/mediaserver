from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse


class UserManagementPermissionsTests(TestCase):
    def setUp(self):
        self.viewer = get_user_model().objects.create_user('viewer', password='test-password')
        self.staff = get_user_model().objects.create_user(
            'admin',
            password='test-password',
            is_staff=True,
        )

    def test_non_staff_cannot_open_user_management(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('account:index'))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_open_user_management(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('account:index'))

        self.assertEqual(response.status_code, 200)

    def test_user_creation_rejects_get(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('account:create_user'))

        self.assertEqual(response.status_code, 405)

    def test_anonymous_user_is_redirected_from_user_management(self):
        response = self.client.get(reverse('account:index'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('account:login'), response.url)

    def test_non_staff_cannot_mutate_users(self):
        self.client.force_login(self.viewer)

        requests = [
            reverse('account:create_user'),
            reverse('account:edit_user', args=[self.staff.id]),
            reverse('account:delete_user', args=[self.staff.id]),
        ]
        for url in requests:
            with self.subTest(url=url):
                response = self.client.post(url, {})
                self.assertEqual(response.status_code, 403)

        self.assertTrue(get_user_model().objects.filter(pk=self.staff.id).exists())

    def test_user_mutations_reject_get_for_staff(self):
        self.client.force_login(self.staff)

        requests = [
            reverse('account:create_user'),
            reverse('account:edit_user', args=[self.viewer.id]),
            reverse('account:delete_user', args=[self.viewer.id]),
        ]
        for url in requests:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 405)

        self.assertTrue(get_user_model().objects.filter(pk=self.viewer.id).exists())

    def test_staff_can_delete_user_with_post(self):
        target = get_user_model().objects.create_user('delete-me', password='test-password')
        self.client.force_login(self.staff)

        response = self.client.post(reverse('account:delete_user', args=[target.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(get_user_model().objects.filter(pk=target.id).exists())
