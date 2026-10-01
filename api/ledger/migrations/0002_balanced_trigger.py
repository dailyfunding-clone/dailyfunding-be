"""분개 그룹 차대 합계 0 제약 — deferred constraint trigger.

같은 group_id에 속한 LedgerEntry의 amount 합계가 0이 아니면 커밋 시점에
롤백된다. deferred이므로 트랜잭션 안에서 다중 leg를 순서대로 INSERT해도 된다.
"""
from django.db import migrations


SQL_UP = """
CREATE OR REPLACE FUNCTION ledger_group_balanced() RETURNS trigger AS $$
BEGIN
    IF (SELECT COALESCE(SUM(amount), 0) FROM ledger_ledgerentry
        WHERE group_id = NEW.group_id) <> 0 THEN
        RAISE EXCEPTION 'ledger group % is not balanced', NEW.group_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER ledger_entry_balanced
AFTER INSERT OR UPDATE ON ledger_ledgerentry
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ledger_group_balanced();
"""

SQL_DOWN = """
DROP TRIGGER IF EXISTS ledger_entry_balanced ON ledger_ledgerentry;
DROP FUNCTION IF EXISTS ledger_group_balanced();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(SQL_UP, reverse_sql=SQL_DOWN),
    ]
