from setuptools import setup, find_packages

setup(
    name="platform-plugin-subscriber",
    version="0.1.0",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "": ["openedx.yaml"],
    },
    entry_points={
        "lms.djangoapp": [
            "subscriber = subscriber.apps:SubscriberConfig",
        ],
    },
)
