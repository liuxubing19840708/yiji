"""产前通用附件上传的格式与认证边界。"""
import shutil
import tempfile

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User


class DocumentUploadTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='preproduction-upload-test-')
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(username='attachment-user', password='pw'))

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_pdf_attachment_is_accepted(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self.client.post('/api/uploads/files', {
            'file': SimpleUploadedFile('工艺单.pdf', b'%PDF-1.4\nattachment test')
        }, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['url'].endswith('.pdf'))

    def test_executable_and_fake_pdf_are_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        executable = self.client.post('/api/uploads/files', {
            'file': SimpleUploadedFile('bad.exe', b'MZ-not-allowed')
        }, format='multipart')
        self.assertEqual(executable.status_code, 400)
        fake_pdf = self.client.post('/api/uploads/files', {
            'file': SimpleUploadedFile('fake.pdf', b'not-a-pdf')
        }, format='multipart')
        self.assertEqual(fake_pdf.status_code, 400)
