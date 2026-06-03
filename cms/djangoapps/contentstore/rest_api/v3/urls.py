"""Contentstore API v3 URLs."""

from rest_framework.routers import DefaultRouter

from cms.djangoapps.contentstore.rest_api.v3.views import HomeViewSet

app_name = "v3"

router = DefaultRouter()
router.register(r'home', HomeViewSet, basename='home')

urlpatterns = router.urls
