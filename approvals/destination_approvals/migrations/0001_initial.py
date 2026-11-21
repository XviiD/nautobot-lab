from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Review",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("rule_id", models.UUIDField(db_index=True)),
                ("snapshot", models.JSONField()),
                ("decisions", models.JSONField(default=dict)),
                ("state", models.CharField(default="pending", max_length=16)),
                ("submitted_by", models.CharField(max_length=255)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-pk"]},
        )
    ]
