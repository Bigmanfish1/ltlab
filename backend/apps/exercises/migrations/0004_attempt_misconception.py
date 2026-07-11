from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('exercises', '0003_topic_uniq_topic_title_ci'),
    ]

    operations = [
        migrations.AddField(
            model_name='attempt',
            name='misconception',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]
