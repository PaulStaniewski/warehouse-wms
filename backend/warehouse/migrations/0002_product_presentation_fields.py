from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("warehouse", "0001_initial")]

    operations = [
        migrations.AddField(model_name="product", name="brand", field=models.CharField(blank=True, max_length=128)),
        migrations.AddField(model_name="product", name="description", field=models.TextField(blank=True)),
        migrations.AddField(model_name="product", name="image_url", field=models.CharField(blank=True, max_length=255)),
    ]
