from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from .services import get_categorized_courses


@login_required
def subscriber_courses(request):
    """
    API endpoint for Subscriber Learner Dashboard.
    Returns user's enrolled courses grouped into categories.
    """
    data = get_categorized_courses(request.user)
    return JsonResponse(data)
