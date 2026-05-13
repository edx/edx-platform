from django.db import migrations, models
import django.utils.translation


class Migration(migrations.Migration):

    dependencies = [
        ('third_party_auth', '0014_samlproviderconfig_optional_email_checkboxes'),
    ]

    operations = [
        migrations.AddField(
            model_name='samlproviderconfig',
            name='disable_email_editing',
            field=models.BooleanField(
                default=False,
                help_text=django.utils.translation.gettext_lazy(
                    "If enabled, and the identity provider supplies an email address, the email field "
                    "on the SSO registration form will be read-only and users will not be able to change "
                    "their email address in their account settings. If the identity provider does not "
                    "supply an email address, the field remains editable during registration."
                ),
            ),
        ),
    ]
