"""
Views for the notifications API.
"""
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count
from django_ratelimit.core import is_ratelimited
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from pytz import UTC
from rest_framework import generics, status
from rest_framework.decorators import api_view
from rest_framework.generics import UpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.notifications.email.utils import (
    update_user_preferences_from_patch,
    username_from_hash
)
from openedx.core.djangoapps.notifications.models import NotificationPreference
from openedx.core.djangoapps.notifications.permissions import (
    allow_any_authenticated_user
)

from .base_notification import (
    COURSE_NOTIFICATION_APPS,
    NotificationAppManager,
    COURSE_NOTIFICATION_TYPES,
    NotificationTypeManager
)
from .events import (
    notification_preference_update_event,
    notification_read_event,
    notification_tray_opened_event,
    notifications_app_all_read_event
)
from .models import Notification
from .serializers import (
    NotificationSerializer,
    UserNotificationPreferenceUpdateAllSerializer,
    add_info_to_notification_config,
    add_non_editable_in_preference
)
from .tasks import create_notification_preference
from .utils import (
    get_show_notifications_tray,
    exclude_inaccessible_preferences
)

logger = logging.getLogger(__name__)


@allow_any_authenticated_user()
class NotificationListAPIView(generics.ListAPIView):
    """
    API view for listing notifications for a user.

    **Permissions**: User must be authenticated.
    **Response Format** (paginated):

        {
            "results" : [
                {
                    "id": (int) notification_id,
                    "app_name": (str) app_name,
                    "notification_type": (str) notification_type,
                    "content": (str) content,
                    "content_context": (dict) content_context,
                    "content_url": (str) content_url,
                    "last_read": (datetime) last_read,
                    "last_seen": (datetime) last_seen
                },
                ...
            ],
            "count": (int) total_number_of_notifications,
            "next": (str) url_to_next_page_of_notifications,
            "previous": (str) url_to_previous_page_of_notifications,
            "page_size": (int) number_of_notifications_per_page,

        }

    Response Error Codes:
    - 403: The requester cannot access resource.
    """

    serializer_class = NotificationSerializer

    def get_queryset(self):
        """
        Override get_queryset to filter by app name, user and created.
        """
        try:
            expiry_date = datetime.now(UTC) - timedelta(
                days=settings.NOTIFICATIONS_EXPIRY
            )
            app_name = self.request.query_params.get('app_name')

            if self.request.query_params.get('tray_opened'):
                unseen_count = Notification.objects.filter(
                    user_id=self.request.user,
                    last_seen__isnull=True
                ).count()
                notification_tray_opened_event(
                    self.request.user,
                    unseen_count
                )
            params = {
                'user': self.request.user,
                'created__gte': expiry_date,
                'web': True
            }

            if app_name:
                params['app_name'] = app_name
            queryset = Notification.objects.filter(
                **params
            ).order_by('-created')
            logger.info(
                'Retrieved notifications for user %s with app_name=%s',
                self.request.user.id,
                app_name,
            )
            return queryset
        except Notification.DoesNotExist as exc:
            logger.error(
                f'Failed to retrieve notifications for user '
                f'{self.request.user.id}: {str(exc)}'
            )
            raise


@allow_any_authenticated_user()
class NotificationCountView(APIView):
    """
    API view for getting unseen notifications count and tray flag.
    """

    def get(self, request):
        """
        Get the unseen notifications count and show_notification_tray flag.

        **Permissions**: User must be authenticated.
        **Response Format**:
        ```json
        {
            "show_notifications_tray": (bool) show_notifications_tray,
            "count": (int) total_number_of_unseen_notifications,
            "count_by_app_name": {
                (str) app_name: (int) number_of_unseen_notifications,
                ...
            },
            "notification_expiry_days": 60
        }
        ```
        **Response Error Codes**:
        - 403: The requester cannot access resource.
        """
        try:
            # Get unseen notifications count for each app name.
            count_by_app_name = (
                Notification.objects
                .filter(
                    user_id=request.user,
                    last_seen__isnull=True,
                    web=True
                )
                .values('app_name')
                .annotate(count=Count('*'))
            )
            count_total = 0
            show_notifications_tray = get_show_notifications_tray(
                self.request.user
            )
            count_by_app_name_dict = {
                app_name: 0
                for app_name in COURSE_NOTIFICATION_APPS
            }

            for item in count_by_app_name:
                app_name = item['app_name']
                count = item['count']
                count_total += count
                count_by_app_name_dict[app_name] = count

            logger.info(
                f'Retrieved notification count for user '
                f'{request.user.id}: total={count_total}'
            )
            return Response({
                "show_notifications_tray": show_notifications_tray,
                "count": count_total,
                "count_by_app_name": count_by_app_name_dict,
                "notification_expiry_days": settings.NOTIFICATIONS_EXPIRY,
            })
        except (Notification.DoesNotExist, AttributeError) as exc:
            logger.error(
                'Failed to retrieve notification count for user %s: %s',
                request.user.id,
                str(exc),
            )
            return Response(
                {'error': 'Failed to retrieve notification count'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@allow_any_authenticated_user()
class MarkNotificationsSeenAPIView(UpdateAPIView):
    """
    API view for marking user notifications seen for app_name.
    """

    def update(self, request, *args, **kwargs):
        """
        Mark all notifications for app name as seen.

        **Args:**
            app_name: The name of the app to mark notifications seen for.
        **Response Format:**
            A `Response` object with 200 OK if notifications marked seen.
        **Response Error Codes**:
        - 400: Bad Request if app name is invalid.
        """
        try:
            app_name = self.kwargs.get('app_name')

            if not app_name:
                logger.warning(
                    f'Invalid app_name provided by user {request.user.id}'
                )
                return Response(
                    {'error': _('Invalid app name.')},
                    status=status.HTTP_400_BAD_REQUEST
                )

            notifications = Notification.objects.filter(
                user=request.user,
                app_name=app_name,
                last_seen__isnull=True,
            )
            update_count = notifications.update(last_seen=datetime.now(UTC))
            logger.info(
                'Marked %d notifications as seen for user %s with app_name=%s',
                update_count,
                request.user.id,
                app_name,
            )
            return Response(
                {'message': _('Notifications marked as seen.')},
                status=status.HTTP_200_OK,
            )
        except (Notification.DoesNotExist, AttributeError, TypeError) as exc:
            logger.error(
                'Failed to mark notifications seen for user %s: %s',
                request.user.id,
                str(exc),
            )
            return Response(
                {'error': _('Failed to mark notifications as seen.')},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@allow_any_authenticated_user()
class NotificationReadAPIView(APIView):
    """
    API view for marking notifications as read.
    """

    def patch(self, request, *args, **kwargs):
        """
        Mark all or single notification as read.

        Requests:
        PATCH /api/notifications/read/

        Parameters:
            request (Request): Request containing app name or notification id.
                {
                    "app_name": (str) app_name,
                    "notification_id": (int) notification_id
                }

        Returns:
        - 200: OK if notification marked read.
        - 400: Bad Request if app name is invalid.
        - 403: Forbidden if user not authenticated.
        - 404: Not Found if notification not found.
        """
        try:
            notification_id = request.data.get('notification_id', None)
            read_at = datetime.now(UTC)

            if notification_id:
                try:
                    notification = get_object_or_404(
                        Notification,
                        pk=notification_id,
                        user=request.user
                    )
                except Notification.DoesNotExist as exc:
                    logger.warning(
                        f'Notification {notification_id} not found for user '
                        f'{request.user.id}: {str(exc)}'
                    )
                    raise
                first_time_read = notification.last_read is None
                notification.last_read = read_at
                notification.save()
                notification_read_event(
                    request.user,
                    notification,
                    first_time_read
                )
                logger.info(
                    f'Marked notification {notification_id} as read for '
                    f'user {request.user.id}'
                )
                return Response(
                    {'message': _('Notification marked read.')},
                    status=status.HTTP_200_OK
                )

            app_name = request.data.get('app_name', '')

            if app_name in COURSE_NOTIFICATION_APPS:
                notifications = Notification.objects.filter(
                    user=request.user,
                    app_name = request.data.get('app_name', '')

                    if app_name in COURSE_NOTIFICATION_APPS:
                        notifications = Notification.objects.filter(
                            user=request.user,
                            app_name=app_name,
                            last_read__isnull=True,
                        )
                        update_count = notifications.update(last_read=read_at)
                        notifications_app_all_read_event(request.user, app_name)
                        logger.info(
                            'Marked %d notifications as read for user %s with app_name=%s',
                            update_count,
                            request.user.id,
                            app_name,
                        )
                        return Response(
                            {'message': _('Notifications marked read.')},
                            status=status.HTTP_200_OK,
                        )

                    logger.warning(
                        'Invalid app_name (%s) or notification_id (%s) from user %s',
                        app_name,
                        notification_id,
                        request.user.id,
                    )
                    return Response(
                        {'error': _('Invalid app_name or notification_id.')},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                except (Notification.DoesNotExist, AttributeError, TypeError) as exc:
                    logger.error(
                        'Failed to mark notification as read for user %s: %s',
                        request.user.id,
                        str(exc),
                    )
                    return Response(
                        {'error': _('Failed to mark notification as read.')},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
        ):
            logger.warning(
                f'Rate limit exceeded for username: {username}'
            )
            return Response(
                {"error": "Too many requests"},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        update_user_preferences_from_patch(username)
        logger.info(
            'Updated preferences for username: %s',
            username,
        )
        return Response({"result": "success"}, status=status.HTTP_200_OK)
    except ValueError as exc:
        logger.error(
            'Failed to update preferences for username %s: %s',
            username,
            str(exc),
        )
        return Response(
            {"error": "Failed to update preferences"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@allow_any_authenticated_user()
class NotificationPreferencesView(APIView):
    """
    API view to retrieve and structure notification preferences.
    """

    def get(self, request):
        """
        Retrieve notification preferences for authenticated user.

        Returns:
            Response: DRF Response with structured notification preferences.
        """
        try:
            user_preferences_qs = NotificationPreference.objects.filter(
                user=request.user
            )
            user_preferences_map = {
                pref.type: pref for pref in user_preferences_qs
            }

            # Ensure all notification types present in user's preferences.
            diff = set(COURSE_NOTIFICATION_TYPES.keys()) - set(
                user_preferences_map.keys()
            )
            missing_types = []
            for missing_type in diff:
                new_pref = create_notification_preference(
                    user_id=request.user.id,
                    notification_type=missing_type,
                )
                missing_types.append(new_pref)
                user_preferences_map[missing_type] = new_pref
            if missing_types:
                NotificationPreference.objects.bulk_create(missing_types)
                logger.info(
                    f'Created {len(missing_types)} missing notification '
                    f'preferences for user {request.user.id}'
                )

            # If no user preferences found, return error response.
            if not user_preferences_map:
                logger.warning(
                    f'No active notification preferences for user '
                    f'{request.user.id}'
                )
                return Response({
                    'status': 'error',
                    'message': 'No active notification preferences found.'
                }, status=status.HTTP_404_NOT_FOUND)

            # Get structured preferences from NotificationAppManager.
            structured_preferences = (
                NotificationAppManager()
                .get_notification_app_preferences()
            )

            for app_name, app_settings in structured_preferences.items():
                notification_types = app_settings.get(
                    'notification_types', {}
                )

                # Process notification types.
                for type_name, type_details in notification_types.items():
                    if type_name == 'core':
                        if structured_preferences[app_name][
                            'core_notification_types'
                        ]:
                            notification_type = (
                                structured_preferences[app_name]
                                ['core_notification_types'][0]
                            )
                        else:
                            notification_type = 'core'
                        user_pref = user_preferences_map.get(
                            notification_type
                        )
                    else:
                        user_pref = user_preferences_map.get(type_name)
                    if user_pref:
                        # Update dictionary for this type.
                        type_details['web'] = user_pref.web
                        type_details['email'] = user_pref.email
                        type_details['push'] = user_pref.push
                        type_details['email_cadence'] = (
                            user_pref.email_cadence
                        )
            exclude_inaccessible_preferences(
                structured_preferences,
                request.user
            )
            structured_preferences = add_non_editable_in_preference(
                add_info_to_notification_config(structured_preferences)
            )
            logger.info(
                'Retrieved notification preferences for user %s',
                request.user.id,
            )
            return Response({
                'status': 'success',
                'message': 'Notification preferences retrieved successfully.',
                'show_preferences': get_show_notifications_tray(
                    self.request.user
                ),
                'data': structured_preferences
            }, status=status.HTTP_200_OK)
        except (NotificationPreference.DoesNotExist, KeyError, AttributeError, TypeError) as exc:
            logger.error(
                'Failed to retrieve notification preferences for user %s: %s',
                request.user.id,
                str(exc),
            )
            return Response({
                'status': 'error',
                'message': 'Failed to retrieve notification preferences.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request):
        """
        Update notification preferences for authenticated user.

        Returns:
            Response: DRF Response indicating success or failure.
        """
        try:
            # Validate incoming data
            serializer = UserNotificationPreferenceUpdateAllSerializer(
                data=request.data
            )
            if not serializer.is_valid():
                logger.warning(
                    f'Invalid serializer data for user {request.user.id}: '
                    f'{serializer.errors}'
                )
                return Response({
                    'status': 'error',
                    'message': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)

            # Get validated data
            validated_data = serializer.validated_data

            # Build query set based on notification type
            query_set = NotificationPreference.objects.filter(
                user_id=request.user.id
            )

            if validated_data['notification_type'] == 'core':
                # Get core notification types for the app
                __, core_types = NotificationTypeManager(
                ).get_notification_app_preference(
                    notification_app=validated_data['notification_app']
                )
                query_set = query_set.filter(type__in=core_types)
            else:
                # Filter by single notification type
                query_set = query_set.filter(
                    type=validated_data['notification_type']
                )

            # Prepare update data
            updated_data = self._prepare_update_data(validated_data)

            # Update preferences
            update_count = query_set.update(**updated_data)

            # Log the event
            self._log_preference_update_event(request.user, validated_data)
            logger.info(
                'Updated %d notification preferences for user %s with app=%s',
                update_count,
                request.user.id,
                validated_data["notification_app"],
            )
            # Prepare and return response
            response_data = self._prepare_response_data(validated_data)
            return Response(response_data, status=status.HTTP_200_OK)
        except (NotificationPreference.DoesNotExist, KeyError, AttributeError, TypeError) as exc:
            logger.error(
                'Failed to update notification preferences for user %s: %s',
                request.user.id,
                str(exc),
            )
            return Response({
                'status': 'error',
                'message': 'Failed to update notification preferences.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _prepare_update_data(self, validated_data):
        """
        Prepare the data dictionary for updating preferences.

        Args:
            validated_data (dict): Validated serializer data

        Returns:
            dict: Dictionary with update data
        """
        try:
            channel = validated_data['notification_channel']

            if channel == 'email_cadence':
                result = {channel: validated_data['email_cadence']}
            else:
                result = {channel: validated_data['value']}
            logger.debug(
                'Prepared update data for channel %s: %s',
                channel,
                result,
            )
            return result
        except KeyError as exc:
            logger.error('Failed to prepare update data: %s', str(exc))
            raise

    def _log_preference_update_event(self, user, validated_data):
        """
        Log the notification preference update event.

        Args:
            user: The user making the update
            validated_data (dict): Validated serializer data
        """
        try:
            event_data = {
                'notification_app': validated_data['notification_app'],
                'notification_type': validated_data['notification_type'],
                'notification_channel': validated_data[
                    'notification_channel'
                ],
                'value': validated_data.get('value'),
                'email_cadence': validated_data.get('email_cadence'),
            }
            notification_preference_update_event(user, [], event_data)
            logger.debug(
                f'Logged preference update event for user {user.id}'
            )
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            logger.error(
                f'Failed to log preference update event for user {user.id}: '
                f'{str(exc)}'
            )

    def _prepare_response_data(self, validated_data):
        """
        Prepare the response data dictionary.

        Args:
            validated_data (dict): Validated serializer data

        Returns:
            dict: Response data dictionary
        """
        email_cadence = validated_data.get('email_cadence', None)
        # Determine the updated value
        updated_value = validated_data.get(
            'value',
            email_cadence if email_cadence else None
        )

        # Determine the channel
        channel = validated_data.get('notification_channel')
        if not channel and validated_data.get('email_cadence'):
            channel = 'email_cadence'

        return {
            'status': 'success',
            'message': 'Notification preferences update completed',
            'show_preferences': get_show_notifications_tray(
                self.request.user
            ),
        
            'data': {
                'updated_value': updated_value,
                'notification_type': validated_data['notification_type'],
                'channel': channel,
                'app': validated_data['notification_app'],
            }
        }
        
