"""Contentstore API v3 URLs."""

from rest_framework.routers import DefaultRouter

from cms.djangoapps.contentstore.rest_api.v3.views import CourseDetailsViewSet, HomeViewSet

app_name = "v3"

router = DefaultRouter()
router.register(r'home', HomeViewSet, basename='home')
router.register(r'course_details', CourseDetailsViewSet, basename='course_details')

urlpatterns = router.urls
