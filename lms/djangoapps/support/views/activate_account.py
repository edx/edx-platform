"""
Support tool for confirming a learner's account.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils.decorators import method_decorator
from rest_framework.generics import GenericAPIView

from common.djangoapps.student.models import Registration
from common.djangoapps.util.json_request import JsonResponse
from lms.djangoapps.support.decorators import require_support_permission

log = logging.getLogger(__name__)


class ActivateAccountView(GenericAPIView):
    """
    Lets support staff confirm a learner's account without the learner opening the activation email.

    POST /support/activate_account/{username_or_email}

    Only users who can use the support tools (global staff or the Support role) may call it. The account's
    activation key is never returned, so support never handles the activation link. Activation goes through
    ``Registration.activate()``, exactly as the email link does, so the same signal, timestamp and log line follow.

    Responses:
        200 {"is_active": true, "already_active": false}  the account was confirmed now
        200 {"is_active": true, "already_active": true}   it was already confirmed; nothing changed
        404 {"error": "user_not_found"}                   no user with that username or email
        404 {"error": "registration_not_found"}           the user has no activation record to complete
        403                                               the caller can't use the support tools
    """
    # This view is excluded from Swagger doc generation because it does not specify a serializer class.
    exclude_from_schema = True

    @method_decorator(require_support_permission)
    def post(self, request, username_or_email):
        """Confirm the given user's account."""
        user = get_user_model().objects.filter(
            Q(username=username_or_email) | Q(email=username_or_email)
        ).first()
        if user is None:
            return JsonResponse({'error': 'user_not_found'}, status=404)

        if user.is_active:
            return JsonResponse({'is_active': True, 'already_active': True})

        with transaction.atomic():
            registration = Registration.objects.select_for_update().filter(user=user).first()
            if registration is None:
                return JsonResponse({'error': 'registration_not_found'}, status=404)
            registration.activate()

        log.info(
            'Support activation: user %s confirmed the account of user %s from the support tools.',
            request.user.id,
            user.id,
        )
        return JsonResponse({'is_active': True, 'already_active': False})
