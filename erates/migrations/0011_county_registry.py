import uuid

from django.db import migrations, models

# Some databases already carry a `counties` table from an unreleased 2025 migration
# (columns: code, description, is_deleted, deleted_at). Keep its rows; add what the model needs.
PREPARE_COUNTIES = """
CREATE TABLE IF NOT EXISTS counties (
    county_id uuid NOT NULL PRIMARY KEY,
    name varchar(100) NOT NULL UNIQUE,
    logo varchar(100) NULL,
    rates_office_email varchar(254) NOT NULL DEFAULT '',
    rates_office_phone varchar(20) NOT NULL DEFAULT '',
    paybill varchar(20) NOT NULL DEFAULT '',
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now()
);
ALTER TABLE counties ADD COLUMN IF NOT EXISTS logo varchar(100) NULL;
ALTER TABLE counties ADD COLUMN IF NOT EXISTS rates_office_email varchar(254) NOT NULL DEFAULT '';
ALTER TABLE counties ADD COLUMN IF NOT EXISTS rates_office_phone varchar(20) NOT NULL DEFAULT '';
ALTER TABLE counties ADD COLUMN IF NOT EXISTS paybill varchar(20) NOT NULL DEFAULT '';
ALTER TABLE counties ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'counties' AND column_name = 'is_deleted') THEN
        ALTER TABLE counties ALTER COLUMN is_deleted SET DEFAULT false;
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [('erates', '0010_accounts_hierarchy')]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(PREPARE_COUNTIES, migrations.RunSQL.noop)],
            state_operations=[
                migrations.CreateModel(
                    name='County',
                    fields=[
                        ('county_id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                        ('name', models.CharField(max_length=100, unique=True)),
                        ('logo', models.ImageField(blank=True, null=True, upload_to='county-logos/')),
                        ('rates_office_email', models.EmailField(blank=True, max_length=254)),
                        ('rates_office_phone', models.CharField(blank=True, max_length=20)),
                        ('paybill', models.CharField(blank=True, help_text='M-Pesa short code rates are paid to', max_length=20)),
                        ('is_active', models.BooleanField(default=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        'verbose_name_plural': 'counties',
                        'db_table': 'counties',
                        'ordering': ['name'],
                    },
                ),
            ],
        ),
    ]
