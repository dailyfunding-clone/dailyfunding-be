from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0005_progress_notify'),
    ]

    operations = [
        migrations.RunSQL(
            """
            CREATE OR REPLACE FUNCTION product_progress_event() RETURNS trigger AS $$
            DECLARE
                progress_id bigint;
            BEGIN
                IF TG_OP = 'INSERT' OR
                   (NEW.raised_amount, NEW.target_amount, NEW.status) IS DISTINCT FROM
                   (OLD.raised_amount, OLD.target_amount, OLD.status) THEN
                    PERFORM pg_advisory_xact_lock(730041);
                    INSERT INTO products_productprogress (product_id, raised_amount, remaining, status)
                    VALUES (NEW.id, NEW.raised_amount, NEW.target_amount - NEW.raised_amount, NEW.status)
                    RETURNING id INTO progress_id;
                    PERFORM pg_notify(
                        'product_progress',
                        json_build_object('progress_id', progress_id, 'product_id', NEW.id)::text
                    );
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
            reverse_sql="""
            CREATE OR REPLACE FUNCTION product_progress_event() RETURNS trigger AS $$
            DECLARE
                progress_id bigint;
            BEGIN
                IF TG_OP = 'INSERT' OR
                   (NEW.raised_amount, NEW.target_amount, NEW.status) IS DISTINCT FROM
                   (OLD.raised_amount, OLD.target_amount, OLD.status) THEN
                    PERFORM pg_advisory_xact_lock(hashtext(NEW.id::text));
                    INSERT INTO products_productprogress (product_id, raised_amount, remaining, status)
                    VALUES (NEW.id, NEW.raised_amount, NEW.target_amount - NEW.raised_amount, NEW.status)
                    RETURNING id INTO progress_id;
                    PERFORM pg_notify(
                        'product_progress',
                        json_build_object('progress_id', progress_id, 'product_id', NEW.id)::text
                    );
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
    ]
