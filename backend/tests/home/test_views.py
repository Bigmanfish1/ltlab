from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.http import Http404
from django.test import RequestFactory, TestCase

from apps.accounts.models import Profile
from apps.home import views


class StudentDetailScopingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.teacher = Profile.objects.create(email="t@x.com", name="T", role=Profile.ROLE_TEACHER)
        self.student = Profile.objects.create(email="s@x.com", name="S", role=Profile.ROLE_STUDENT)

    def _req(self, profile):
        request = self.factory.get("/")
        request.profile = profile
        request.supabase_user = type("U", (), {"id": "u", "email": profile.email})()
        request.session = SessionStore()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_teacher_id_404s(self):
        with self.assertRaises(Http404):
            views.teacher_student_detail(self._req(self.teacher), self.teacher.id)

    def test_student_id_renders(self):
        response = views.teacher_student_detail(self._req(self.teacher), self.student.id)
        self.assertEqual(response.status_code, 200)
