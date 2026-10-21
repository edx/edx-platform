from django.urls import path
from .views import subscriber_courses

urlpatterns = [
    path(
    "dashboard/courses/",
    subscriber_courses,
    name="subscriber-dashboard-courses",
    ),
]