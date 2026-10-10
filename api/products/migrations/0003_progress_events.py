from django.db import migrations


SQL_UP = """
CREATE FUNCTION product_progress_event() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' OR
       (NEW.raised_amount, NEW.target_amount, NEW.status) IS DISTINCT FROM
       (OLD.raised_amount, OLD.target_amount, OLD.status) THEN
        -- ponytail: serialize event commits; partition cursors if write throughput grows.
        PERFORM pg_advisory_xact_lock(730041);
        INSERT INTO products_productprogress (product_id, raised_amount, remaining, status)
        VALUES (NEW.id, NEW.raised_amount, NEW.target_amount - NEW.raised_amount, NEW.status);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER product_progress_event
AFTER INSERT OR UPDATE ON products_product
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION product_progress_event();

INSERT INTO products_productprogress (product_id, raised_amount, remaining, status)
SELECT id, raised_amount, target_amount - raised_amount, status FROM products_product;
"""


class Migration(migrations.Migration):
    dependencies = [("products", "0002_productprogress")]

    operations = [
        migrations.RunSQL(
            SQL_UP,
            reverse_sql="""
                DROP TRIGGER product_progress_event ON products_product;
                DROP FUNCTION product_progress_event();
            """,
        ),
    ]
