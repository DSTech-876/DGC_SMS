#!/usr/bin/env python3
"""Idempotent database migration script for DGC SMS.

Adds any columns required by the current codebase that are missing from the
existing SQLite database.  Safe to run repeatedly — columns that already
exist are silently skipped.

Usage (from project root):
    python migrate_db.py              # uses instance/dgc_sms.db
    python migrate_db.py /path/to/db  # explicit path
"""

import sqlite3
import sys
import os

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'instance', 'dgc_sms.db'
)

# ---- Schema additions -------------------------------------------------------
# Each entry: (table, column_name, column_definition)

MIGRATIONS = [
    # samples – summary report
    ('samples', 'summary_report', 'TEXT'),
    ('samples', 'summary_report_file', 'VARCHAR(500)'),
    ('samples', 'summary_report_file_original_name', 'VARCHAR(255)'),
    ('samples', 'summary_report_by', 'INTEGER REFERENCES users(id)'),
    ('samples', 'summary_report_at', 'DATETIME'),
    # samples – deputy review
    ('samples', 'deputy_review_comments', 'TEXT'),
    ('samples', 'deputy_reviewed_by', 'INTEGER REFERENCES users(id)'),
    ('samples', 'deputy_reviewed_at', 'DATETIME'),
    # samples – certificate
    ('samples', 'certificate_text', 'TEXT'),
    ('samples', 'certificate_file', 'VARCHAR(500)'),
    ('samples', 'certificate_file_original_name', 'VARCHAR(255)'),
    ('samples', 'certificate_prepared_by', 'INTEGER REFERENCES users(id)'),
    ('samples', 'certificate_prepared_at', 'DATETIME'),
    # samples – HOD review / certification
    ('samples', 'hod_review_comments', 'TEXT'),
    ('samples', 'hod_reviewed_by', 'INTEGER REFERENCES users(id)'),
    ('samples', 'hod_reviewed_at', 'DATETIME'),
    ('samples', 'certified_at', 'DATETIME'),
    ('samples', 'certified_by', 'INTEGER REFERENCES users(id)'),
    # users – force password change on first login
    ('users', 'must_change_password', 'BOOLEAN DEFAULT 0'),
    # acting_roles – opt-in activation switch
    ('acting_roles', 'is_activated', 'BOOLEAN NOT NULL DEFAULT 0'),
    ('acting_roles', 'activated_at', 'DATETIME'),
    # users – active flag and creation timestamp
    ('users', 'is_active_user', 'BOOLEAN DEFAULT 1'),
    ('users', 'created_at', 'DATETIME'),
    # sample_assignments – preliminary review
    ('sample_assignments', 'preliminary_review_comments', 'TEXT'),
    ('sample_assignments', 'preliminary_reviewed_by', 'INTEGER REFERENCES users(id)'),
    ('sample_assignments', 'preliminary_reviewed_at', 'DATETIME'),
    ('sample_assignments', 'return_stage', 'VARCHAR(20)'),
    ('sample_assignments', 'preliminary_review_checklist', 'TEXT'),
    # samples – milk type (Food Milk)
    ('samples', 'milk_type', 'VARCHAR(10)'),
    # samples – new type-specific fields
    ('samples', 'volume', 'VARCHAR(100)'),
    ('samples', 'formulation_type', 'VARCHAR(100)'),
    ('samples', 'api', 'VARCHAR(255)'),
    ('samples', 'alcohol_type', 'VARCHAR(100)'),
    ('samples', 'claim_butt_number', 'VARCHAR(100)'),
    # samples – expected report date
    ('samples', 'expected_report_date', 'DATE'),
    # sample_assignments – report return fields
    ('sample_assignments', 'all_samples_returned', 'VARCHAR(10)'),
    ('sample_assignments', 'return_quantity', 'VARCHAR(100)'),
    # sample_assignments – assignment comments and quantity/volume
    ('sample_assignments', 'comments', 'TEXT'),
    ('sample_assignments', 'quantity_volume', 'VARCHAR(100)'),
    # samples – new fields for requirements
    ('samples', 'batch_lot_number', 'VARCHAR(100)'),
    ('samples', 'lot_number', 'VARCHAR(100)'),
    ('samples', 'expiration_date', 'DATE'),
    ('samples', 'toxicology_sample_type_name', 'VARCHAR(100)'),
    # samples - scanned file original name
    ('samples', 'scanned_file_original_name', 'VARCHAR(255)'),
    # samples – COA reference
    ('samples', 'coa_reference', 'VARCHAR(255)'),
    # sample_history – enhanced audit fields
    ('sample_history', 'action_type', 'VARCHAR(100)'),
    ('sample_history', 'object_affected', 'VARCHAR(255)'),
    ('sample_history', 'change_description', 'TEXT'),
    # users – account lockout (brute-force protection)
    ('users', 'failed_login_attempts', 'INTEGER DEFAULT 0'),
    ('users', 'locked_until', 'DATETIME'),
    # users – activity tracking
    ('users', 'last_seen', 'DATETIME'),
    # sample_assignments – report metadata (test date, spec compliance, comments)
    ('sample_assignments', 'test_date', 'DATE'),
    ('sample_assignments', 'meets_specifications', 'VARCHAR(20)'),
    ('sample_assignments', 'report_comments', 'TEXT'),
    # sample_assignments – Senior Chemist out-of-spec flag
    ('sample_assignments', 'out_of_spec', 'BOOLEAN'),
    # back_date_requests – optional link to a specific assignment
    ('back_date_requests', 'assignment_id', 'INTEGER REFERENCES sample_assignments(id)'),
    # samples – toxicology clinical fields
    ('samples', 'doctors_name', 'VARCHAR(255)'),
    ('samples', 'registration_docket_no', 'VARCHAR(100)'),
    ('samples', 'patient_gender', 'VARCHAR(20)'),
    ('samples', 'ward_clinic', 'VARCHAR(255)'),
    ('samples', 'test_requested', 'VARCHAR(500)'),
    ('samples', 'diagnosis_indicated', 'TEXT'),
    # Feature 4 – pharmaceutical API (active ingredient) dropdown
    ('samples', 'active_ingredient', 'VARCHAR(255)'),
    # Feature 1 – OOS Investigation flag on assignment
    ('sample_assignments', 'oos_investigation', 'BOOLEAN DEFAULT 0'),
    # Feature 5 – COA decertify / re-issue audit fields
    ('samples', 'coa_version', 'INTEGER NOT NULL DEFAULT 1'),
    ('samples', 'decertified_at', 'DATETIME'),
    ('samples', 'decertified_by', 'INTEGER REFERENCES users(id)'),
    ('samples', 'decertify_reason', 'TEXT'),
    ('samples', 'reissued_at', 'DATETIME'),
    ('samples', 'reissued_by', 'INTEGER REFERENCES users(id)'),
    # return-to-analyst comment (ad-hoc return from sample detail page)
    ('sample_assignments', 'return_to_analyst_comments', 'TEXT'),
    # Feature 11 – branch-specific dropdown entries (test types per laboratory)
    ('dropdown_configs', 'branch', 'VARCHAR(100)'),
    # Pharmaceutical – manufacturer field
    ('samples', 'manufacturer', 'VARCHAR(255)'),
    # HOD Workflow Return fields
    ('samples', 'returned_to_stage', 'VARCHAR(50)'),
    ('samples', 'hod_return_reason', 'TEXT'),
    ('samples', 'returned_by_hod_at', 'DATETIME'),
    # Enhanced Audit Log fields
    ('audit_log', 'human_description', 'TEXT'),
    ('audit_log', 'user_role', 'VARCHAR(50)'),
    ('audit_log', 'user_department', 'VARCHAR(100)'),
    ('audit_log', 'report_type', 'VARCHAR(50)'),
    ('audit_log', 'previous_value', 'TEXT'),
    ('audit_log', 'new_value', 'TEXT'),
    ('audit_log', 'previous_stage', 'VARCHAR(50)'),
    ('audit_log', 'new_stage', 'VARCHAR(50)'),
    ('audit_log', 'comments', 'TEXT'),
    ('audit_log', 'ip_address', 'VARCHAR(45)'),
    ('audit_log', 'user_agent', 'VARCHAR(500)'),
    ('audit_log', 'success', 'BOOLEAN DEFAULT 1'),
    # Accreditation status (Deputy selects before certifying)
    ('samples', 'is_accredited', 'BOOLEAN'),
    # Resubmission type classification (which review stage triggered the resubmission)
    ('document_versions', 'resubmission_type', 'VARCHAR(50)'),
    # Post-certification Out-of-Spec marking (HOD / Deputy / Senior Chemist)
    ('samples', 'marked_oos_at', 'DATETIME'),
    ('samples', 'marked_oos_by', 'INTEGER REFERENCES users(id)'),
    ('samples', 'oos_mark_reason', 'TEXT'),
]

NEW_TABLES = [
    (
        'settings',
        'CREATE TABLE IF NOT EXISTS settings ('
        '  key VARCHAR(120) PRIMARY KEY,'
        '  value VARCHAR(500) NOT NULL DEFAULT ""'
        ')',
    ),
    (
        'user_roles',
        'CREATE TABLE IF NOT EXISTS user_roles ('
        '  user_id INTEGER NOT NULL REFERENCES users(id),'
        '  role VARCHAR(20) NOT NULL,'
        '  PRIMARY KEY (user_id, role)'
        ')',
    ),
    (
        'user_branches',
        'CREATE TABLE IF NOT EXISTS user_branches ('
        '  user_id INTEGER NOT NULL REFERENCES users(id),'
        '  branch VARCHAR(20) NOT NULL,'
        '  PRIMARY KEY (user_id, branch)'
        ')',
    ),
    (
        'user_permissions',
        'CREATE TABLE IF NOT EXISTS user_permissions ('
        '  user_id INTEGER NOT NULL REFERENCES users(id),'
        '  permission VARCHAR(50) NOT NULL,'
        '  PRIMARY KEY (user_id, permission)'
        ')',
    ),
    (
        'custom_roles',
        'CREATE TABLE IF NOT EXISTS custom_roles ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  name VARCHAR(120) NOT NULL UNIQUE,'
        '  description VARCHAR(255),'
        '  created_at DATETIME'
        ')',
    ),
    (
        'custom_role_permissions',
        'CREATE TABLE IF NOT EXISTS custom_role_permissions ('
        '  custom_role_id INTEGER NOT NULL REFERENCES custom_roles(id),'
        '  permission VARCHAR(50) NOT NULL,'
        '  PRIMARY KEY (custom_role_id, permission)'
        ')',
    ),
    (
        'user_custom_roles',
        'CREATE TABLE IF NOT EXISTS user_custom_roles ('
        '  user_id INTEGER NOT NULL REFERENCES users(id),'
        '  custom_role_id INTEGER NOT NULL REFERENCES custom_roles(id),'
        '  PRIMARY KEY (user_id, custom_role_id)'
        ')',
    ),
    (
        'notifications',
        'CREATE TABLE IF NOT EXISTS notifications ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  user_id INTEGER NOT NULL REFERENCES users(id),'
        '  title VARCHAR(255) NOT NULL,'
        '  message TEXT NOT NULL,'
        '  link VARCHAR(500),'
        '  is_read BOOLEAN DEFAULT 0,'
        '  email_sent BOOLEAN DEFAULT 0,'
        '  created_at DATETIME'
        ')',
    ),
    (
        'kpi_targets',
        'CREATE TABLE IF NOT EXISTS kpi_targets ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  year INTEGER NOT NULL,'
        '  quarter INTEGER NOT NULL,'
        '  kpi_key VARCHAR(100) NOT NULL,'
        '  target_value FLOAT,'
        '  actual_override FLOAT,'
        '  UNIQUE (year, quarter, kpi_key)'
        ')',
    ),
    (
        'non_working_days',
        'CREATE TABLE IF NOT EXISTS non_working_days ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  date DATE NOT NULL UNIQUE,'
        '  description VARCHAR(255) NOT NULL,'
        '  day_type VARCHAR(50) NOT NULL DEFAULT "holiday",'
        '  created_by INTEGER REFERENCES users(id),'
        '  created_at DATETIME'
        ')',
    ),
    (
        'supporting_documents',
        'CREATE TABLE IF NOT EXISTS supporting_documents ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  sample_id INTEGER NOT NULL REFERENCES samples(id),'
        '  file_path VARCHAR(500) NOT NULL,'
        '  original_name VARCHAR(255) NOT NULL,'
        '  description VARCHAR(500),'
        '  uploaded_by INTEGER NOT NULL REFERENCES users(id),'
        '  uploaded_at DATETIME'
        ')',
    ),
    (
        'document_versions',
        'CREATE TABLE IF NOT EXISTS document_versions ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  sample_id INTEGER NOT NULL REFERENCES samples(id),'
        '  document_type VARCHAR(50) NOT NULL,'
        '  version_number INTEGER NOT NULL DEFAULT 1,'
        '  file_path VARCHAR(500) NOT NULL,'
        '  original_name VARCHAR(255) NOT NULL,'
        '  upload_label VARCHAR(50),'
        '  uploaded_by INTEGER NOT NULL REFERENCES users(id),'
        '  uploaded_at DATETIME,'
        '  assignment_id INTEGER REFERENCES sample_assignments(id)'
        ')',
    ),
    (
        'back_date_requests',
        'CREATE TABLE IF NOT EXISTS back_date_requests ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  sample_id INTEGER NOT NULL REFERENCES samples(id),'
        '  field_name VARCHAR(100) NOT NULL,'
        '  original_date VARCHAR(50) NOT NULL,'
        '  proposed_date VARCHAR(50) NOT NULL,'
        '  reason TEXT,'
        '  requested_by INTEGER NOT NULL REFERENCES users(id),'
        '  requested_at DATETIME,'
        '  status VARCHAR(20) NOT NULL DEFAULT "pending",'
        '  decided_by INTEGER REFERENCES users(id),'
        '  decided_at DATETIME,'
        '  decision_comments TEXT'
        ')',
    ),
    (
        'review_history',
        'CREATE TABLE IF NOT EXISTS review_history ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  sample_id INTEGER NOT NULL REFERENCES samples(id),'
        '  assignment_id INTEGER REFERENCES sample_assignments(id),'
        '  review_type VARCHAR(50) NOT NULL,'
        '  review_number INTEGER NOT NULL DEFAULT 1,'
        '  action VARCHAR(50) NOT NULL,'
        '  reviewer_id INTEGER NOT NULL REFERENCES users(id),'
        '  reviewed_at DATETIME,'
        '  comments TEXT,'
        '  checklist_data TEXT'
        ')',
    ),
    (
        'audit_log',
        'CREATE TABLE IF NOT EXISTS audit_log ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  action VARCHAR(100) NOT NULL,'
        '  entity_type VARCHAR(100) NOT NULL,'
        '  entity_id INTEGER,'
        '  entity_label VARCHAR(255),'
        '  details TEXT,'
        '  performed_by INTEGER NOT NULL REFERENCES users(id),'
        '  performed_at DATETIME'
        ')',
    ),
    (
        'delete_requests',
        'CREATE TABLE IF NOT EXISTS delete_requests ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  request_type VARCHAR(20) NOT NULL,'
        '  sample_id INTEGER REFERENCES samples(id),'
        '  assignment_id INTEGER REFERENCES sample_assignments(id),'
        '  reason TEXT,'
        '  entity_snapshot TEXT,'
        '  requested_by INTEGER NOT NULL REFERENCES users(id),'
        '  requested_at DATETIME,'
        '  status VARCHAR(20) NOT NULL DEFAULT "pending",'
        '  decided_by INTEGER REFERENCES users(id),'
        '  decided_at DATETIME,'
        '  decision_comments TEXT,'
        '  entity_label VARCHAR(255)'
        ')',
    ),
    (
        'direct_messages',
        'CREATE TABLE IF NOT EXISTS direct_messages ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  sender_id INTEGER NOT NULL REFERENCES users(id),'
        '  recipient_id INTEGER NOT NULL REFERENCES users(id),'
        '  body TEXT NOT NULL,'
        '  is_read BOOLEAN DEFAULT 0,'
        '  created_at DATETIME'
        ')',
    ),
    # Feature 9 – Invoices
    (
        'invoices',
        'CREATE TABLE IF NOT EXISTS invoices ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  sample_id INTEGER NOT NULL REFERENCES samples(id),'
        '  invoice_number VARCHAR(50) NOT NULL UNIQUE,'
        '  created_by INTEGER NOT NULL REFERENCES users(id),'
        '  created_at DATETIME,'
        '  notes TEXT'
        ')',
    ),
    (
        'invoice_items',
        'CREATE TABLE IF NOT EXISTS invoice_items ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  invoice_id INTEGER NOT NULL REFERENCES invoices(id),'
        '  test_name VARCHAR(255) NOT NULL,'
        '  test_type VARCHAR(100),'
        '  unit_cost NUMERIC(10,2) NOT NULL DEFAULT 0,'
        '  quantity INTEGER NOT NULL DEFAULT 1'
        ')',
    ),
    # Feature 11 – Dropdown Configuration
    (
        'dropdown_configs',
        'CREATE TABLE IF NOT EXISTS dropdown_configs ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  category VARCHAR(100) NOT NULL,'
        '  value VARCHAR(255) NOT NULL,'
        '  label VARCHAR(255),'
        '  branch VARCHAR(100),'
        '  sort_order INTEGER NOT NULL DEFAULT 0,'
        '  is_active BOOLEAN NOT NULL DEFAULT 1,'
        '  created_by INTEGER REFERENCES users(id),'
        '  created_at DATETIME,'
        '  UNIQUE (category, value, branch)'
        ')',
    ),
    # Sample Comments
    (
        'sample_comments',
        'CREATE TABLE IF NOT EXISTS sample_comments ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  sample_id INTEGER NOT NULL REFERENCES samples(id),'
        '  user_id INTEGER NOT NULL REFERENCES users(id),'
        '  comment_text TEXT NOT NULL,'
        '  created_at DATETIME,'
        '  updated_at DATETIME,'
        '  is_deleted BOOLEAN DEFAULT 0,'
        '  deleted_by INTEGER REFERENCES users(id),'
        '  deleted_at DATETIME'
        ')',
    ),
    # Sample Images
    (
        'sample_images',
        'CREATE TABLE IF NOT EXISTS sample_images ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  sample_id INTEGER NOT NULL REFERENCES samples(id),'
        '  file_path VARCHAR(500) NOT NULL,'
        '  original_name VARCHAR(255) NOT NULL,'
        '  caption VARCHAR(500),'
        '  uploaded_by INTEGER NOT NULL REFERENCES users(id),'
        '  uploaded_at DATETIME'
        ')',
    ),
    # Acting Roles – temporary role assignments with expiry
    (
        'acting_roles',
        'CREATE TABLE IF NOT EXISTS acting_roles ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,'
        '  role VARCHAR(50) NOT NULL,'
        '  assigned_by INTEGER NOT NULL REFERENCES users(id),'
        '  start_date DATE NOT NULL,'
        '  expiry_date DATE NOT NULL,'
        '  notes VARCHAR(500),'
        '  created_at DATETIME,'
        '  is_activated BOOLEAN NOT NULL DEFAULT 0,'
        '  activated_at DATETIME'
        ')',
    ),
]

# ------------------------------------------------------------------------------


def _existing_columns(cursor, table):
    cursor.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cursor.fetchall()}


def _table_exists(cursor, table):
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def _column_exists(cursor, table, column):
    if not _table_exists(cursor, table):
        return False
    return column in _existing_columns(cursor, table)


def migrate(db_path):
    if not os.path.exists(db_path):
        print(f'Database not found at {db_path} — nothing to migrate.')
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create any missing tables
    for table_name, ddl in NEW_TABLES:
        cur.execute(ddl)
        print(f'  Table  {table_name}: ensured')

    # Add any missing columns
    cache = {}
    missing_tables_logged = set()
    for table, col, typedef in MIGRATIONS:
        if not _table_exists(cur, table):
            if table not in missing_tables_logged:
                print(f'  Table  {table}: missing, skipped columns')
                missing_tables_logged.add(table)
            continue
        if table not in cache:
            cache[table] = _existing_columns(cur, table)
        if col in cache[table]:
            continue
        cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {typedef}')
        cache[table].add(col)
        print(f'  Column {table}.{col}: added')

    _backfill_pharma_api(cur)
    _backfill_resubmission_type(cur)

    conn.commit()

    # Migrate data from legacy single-value columns to association tables
    _migrate_roles_branches(cur)

    # Make legacy role/branch columns nullable (they may have NOT NULL from
    # the original schema, but the multi-role system no longer requires them).
    _make_legacy_columns_nullable(cur)

    conn.commit()
    conn.close()
    print('Migration complete.')


def _extract_api_candidate(description, sample_name):
    """Try to derive an API string from legacy text fields."""
    for raw in (description or '', sample_name or ''):
        text = (raw or '').strip()
        if not text:
            continue

        lower = text.lower()
        for marker in ('api:', 'active ingredient:', 'active pharmaceutical ingredient:'):
            idx = lower.find(marker)
            if idx != -1:
                candidate = text[idx + len(marker):].strip(' .;,-')
                if candidate:
                    return candidate[:255]

        # Conservative fallback for short values commonly used as API labels.
        if len(text) <= 120 and len(text.split()) <= 6:
            return text[:255]

    return None


def _backfill_resubmission_type(cur):
    """Backfill document_versions.resubmission_type for existing resubmission rows.

    Rows created before the resubmission_type column was added have a NULL
    value.  These are historical resubmissions whose review stage is unknown,
    so we classify them as 'unspecified' so they are visible when the
    'Unspecified Review' filter is selected instead of disappearing from all
    per-type counts.
    """
    if not _column_exists(cur, 'document_versions', 'resubmission_type'):
        print('  Backfill document_versions.resubmission_type skipped (column missing)')
        return

    cur.execute(
        '''
        UPDATE document_versions
        SET resubmission_type = 'unspecified'
        WHERE document_type = 'report'
          AND upload_label = 'resubmission'
          AND resubmission_type IS NULL
        '''
    )
    updated = cur.rowcount
    print(f'  Backfill document_versions.resubmission_type: updated {updated} row(s)')


def _backfill_pharma_api(cur):
    """Backfill samples.api for existing pharma rows where API is empty."""
    if not _column_exists(cur, 'samples', 'api'):
        print('  Backfill samples.api skipped (samples/api column missing)')
        return

    cur.execute(
        '''
        SELECT id, sample_type, sample_name, description, api
        FROM samples
        WHERE api IS NULL OR TRIM(api) = ''
        '''
    )
    rows = cur.fetchall()

    updated = 0
    pharma_types = {
        'PHARMACEUTICAL',
        'PHARMACEUTICAL_NR',
        'Pharmaceutical',
        'Not Registered Pharm',
    }
    for sample_id, sample_type, sample_name, description, _api in rows:
        if sample_type not in pharma_types:
            continue
        candidate = _extract_api_candidate(description, sample_name)
        if not candidate:
            continue
        cur.execute(
            'UPDATE samples SET api = ? WHERE id = ? AND (api IS NULL OR TRIM(api) = "")',
            (candidate, sample_id),
        )
        updated += cur.rowcount

    print(f'  Backfill samples.api: updated {updated} row(s)')


def _migrate_roles_branches(cur):
    """Copy role/branch from legacy columns into user_roles/user_branches tables.
    Only inserts rows that don't already exist."""
    if not _table_exists(cur, 'users'):
        print('  Legacy users table missing — skipping role/branch copy')
        return

    # Migrate roles
    cur.execute(
        'SELECT id, role FROM users WHERE role IS NOT NULL'
    )
    for user_id, role in cur.fetchall():
        cur.execute(
            'INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, ?)',
            (user_id, role),
        )
    print('  Legacy roles migrated to user_roles')

    # Migrate branches
    cur.execute(
        'SELECT id, branch FROM users WHERE branch IS NOT NULL'
    )
    for user_id, branch in cur.fetchall():
        cur.execute(
            'INSERT OR IGNORE INTO user_branches (user_id, branch) VALUES (?, ?)',
            (user_id, branch),
        )
    print('  Legacy branches migrated to user_branches')


def _make_legacy_columns_nullable(cur):
    """Recreate the users table so that the legacy 'role' and 'branch' columns
    are nullable.  SQLite doesn't support ALTER COLUMN, so we have to do the
    standard copy-rename dance.  This is idempotent: if the columns are already
    nullable, the operation is a harmless no-op."""
    if not _table_exists(cur, 'users'):
        print('  Legacy users table missing — skipping nullable migration')
        return

    # Check whether migration is needed
    cur.execute('PRAGMA table_info("users")')
    cols_info = cur.fetchall()
    needs_fix = False
    for col in cols_info:
        # col = (cid, name, type, notnull, dflt_value, pk)
        if col[1] in ('role', 'branch') and col[3] == 1:  # notnull == 1
            needs_fix = True
            break
    if not needs_fix:
        print('  Legacy columns already nullable — skipping')
        return

    # Build column list from existing schema
    col_names = [col[1] for col in cols_info]
    col_list = ', '.join(f'"{c}"' for c in col_names)

    # Build CREATE TABLE with the same columns but role/branch now nullable
    col_defs = []
    for col in cols_info:
        cid, name, ctype, notnull, dflt, pk = col
        parts = [f'"{name}"', ctype or 'TEXT']
        if pk:
            parts.append('PRIMARY KEY')
        if notnull and name not in ('role', 'branch'):
            parts.append('NOT NULL')
        if dflt is not None:
            parts.append(f'DEFAULT {dflt}')
        col_defs.append(' '.join(parts))

    create_sql = (
        'CREATE TABLE "users_new" (\n  '
        + ',\n  '.join(col_defs)
        + '\n)'
    )

    cur.execute(create_sql)
    cur.execute(f'INSERT INTO "users_new" ({col_list}) SELECT {col_list} FROM "users"')
    cur.execute('DROP TABLE "users"')
    cur.execute('ALTER TABLE "users_new" RENAME TO "users"')
    # Recreate indexes
    cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)')
    cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)')
    print('  Legacy role/branch columns made nullable')


if __name__ == '__main__':
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    print(f'Migrating {db_path} ...')
    migrate(db_path)
