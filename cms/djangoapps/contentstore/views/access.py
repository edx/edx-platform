""" Helper methods for determining user access permissions in Studio """

from functools import wraps

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import JSONParser
from rest_framework.request import Request

from common.djangoapps.student import auth
from common.djangoapps.student.roles import CourseInstructorRole


def allow_jwt_cookie_auth(view_func):
    """
    Let a session-based Studio view also accept the JWT cookie sent by the authoring MFE.

    The MFE signs in through the LMS and sends the JWT cookie with every request, so it
    can call Studio before the user has a Studio session. Without that session,
    @login_required redirects the request to the LMS sign-in, the browser blocks that
    cross-origin redirect, and the MFE shows a load or save error.

    Put it above @login_required. It does nothing when the user already has a Studio
    session. It checks the cookie with the same JwtAuthentication class (token and
    CSRF) that the Studio REST APIs use, and it does not create a session.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            user = _get_jwt_cookie_user(request)
            if user is not None:
                request.user = user
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def _get_jwt_cookie_user(request):
    """
    Return the user of a valid JWT cookie on this request, or None.
    """
    drf_request = Request(request, parsers=[JSONParser()])
    if not JwtAuthentication.is_authenticating_with_jwt_cookie(drf_request):
        return None

    # The CSRF check in JwtAuthentication reads POST data through the DRF request.
    # Read the body first, so that check parses a copy and @expect_json can still
    # read request.body in the view.
    request.body  # pylint: disable=pointless-statement
    try:
        user_and_token = JwtAuthentication().authenticate(drf_request)
    except AuthenticationFailed:
        return None
    return user_and_token[0] if user_and_token else None


def get_user_role(user, course_id):
    """
    What type of access: staff or instructor does this user have in Studio?

    No code should use this for access control, only to quickly serialize the type of access
    where this code knows that Instructor trumps Staff and assumes the user has one or the other.

    This will not return student role because its purpose for using in Studio.

    :param course_id: the course_id of the course we're interested in
    """
    # afaik, this is only used in lti
    if auth.user_has_role(user, CourseInstructorRole(course_id)):
        return 'instructor'
    else:
        return 'staff'
