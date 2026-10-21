from django.apps import AppConfig


class SubscriberConfig(AppConfig):
    name = "subscriber"

    plugin_app = {
        "url_config": {
            "lms.djangoapp": {
                "namespace": "subscriber",
                "regex": "^api/subscriber/",
                "relative_path": "urls",
            }
        }
    }
