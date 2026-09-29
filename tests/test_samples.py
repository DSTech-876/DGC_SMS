import io
import json
from datetime import date, datetime, timedelta
from app import db
from app.models import (
    Sample, SampleAssignment, User, Role, Branch,
    SampleStatus, AssignmentStatus, Setting, SampleHistory, ReviewHistory,
    Permission, AuditLog, user_permissions,
)
from tests.conftest import _create_user, _login


# Minimal valid PDF bytes for use in file upload tests
_MINIMAL_PDF = (
    b'%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
    b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
    b'3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\n'
    b'xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n'
    b'0000000058 00000 n\n0000000115 00000 n\n'
    b'trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF'
)


def _report_file():
    """Return a (BytesIO, filename) tuple suitable for test file uploads."""
    return (io.BytesIO(_MINIMAL_PDF), 'report.pdf')


def _setup_users(app):
    """Create officer, senior chemist, chemist, deputy, and hod."""
    with app.app_context():
        officer = _create_user(Role.OFFICER, username='officer')
        sc = _create_user(
            Role.SENIOR_CHEMIST, Branch.TOXICOLOGY, username='senior'
        )
        chemist = _create_user(
            Role.CHEMIST, Branch.TOXICOLOGY, username='chemist'
        )
        deputy = _create_user(Role.DEPUTY, username='deputy')
        hod = _create_user(Role.HOD, username='hod')
        return officer.id, sc.id, chemist.id, deputy.id, hod.id


def _register_sample(client):
    """Register a sample via the form."""
    return client.post('/samples/register', data={
        'lab_number': 'TOX-001',
        'sample_name': 'Test Substance',
        'sample_type': 'TOXICOLOGY',
        'date_received': '2026-01-15',
        'description': 'Test sample',
        'quantity': '50ml',
    }, follow_redirects=True)


def test_register_sample(app, client):
    _setup_users(app)
    _login(client, 'officer')
    resp = _register_sample(client)
    assert resp.status_code == 200
    assert b'TOX-001' in resp.data


def test_sample_list(app, client):
    _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    resp = client.get('/samples/')
    assert resp.status_code == 200
    assert b'TOX-001' in resp.data


def test_sample_detail(app, client):
    _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    with app.app_context():
        sample = Sample.query.first()
    resp = client.get(f'/samples/{sample.id}')
    assert resp.status_code == 200
    assert b'Test Substance' in resp.data


def test_sample_detail_review_and_activity_pagination(app, client):
    _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)

    with app.app_context():
        sample = Sample.query.first()
        sample_id = sample.id
        officer = User.query.filter_by(username='officer').first()
        base = datetime(2026, 1, 1, 0, 0, 0)

        for i in range(12):
            db.session.add(SampleHistory(
                sample_id=sample.id,
                action=f'Activity {i}',
                performed_by=officer.id,
                created_at=base + timedelta(minutes=i),
            ))
            db.session.add(ReviewHistory(
                sample_id=sample.id,
                review_type='technical',
                review_number=i + 1,
                action='approved',
                reviewer_id=officer.id,
                reviewed_at=base + timedelta(minutes=i),
                comments=f'Review {i}',
            ))
        db.session.commit()

    resp = client.get(f'/samples/{sample_id}')
    assert resp.status_code == 200
    assert b'Review 11' in resp.data
    assert b'Review 0' not in resp.data
    assert b'Activity 11' in resp.data
    assert b'Activity 0' not in resp.data
    assert b'review_page=2' in resp.data
    assert b'activity_page=2' in resp.data

    resp = client.get(f'/samples/{sample_id}?review_page=2&activity_page=2')
    assert resp.status_code == 200
    assert b'Review 0' in resp.data
    assert b'Review 11' not in resp.data
    assert b'Activity 0' in resp.data
    assert b'Activity 11' not in resp.data


def test_assignment_detail_review_pagination(app, client):
    _, _, chemist_id, _, _ = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)

    with app.app_context():
        sample = Sample.query.first()
        officer = User.query.filter_by(username='officer').first()
        assignment = SampleAssignment(
            sample_id=sample.id,
            chemist_id=chemist_id,
            assigned_by=officer.id,
            test_name='Pagination Test',
        )
        db.session.add(assignment)
        db.session.flush()

        base = datetime(2026, 1, 1, 0, 0, 0)
        for i in range(12):
            db.session.add(ReviewHistory(
                sample_id=sample.id,
                assignment_id=assignment.id,
                review_type='technical',
                review_number=i + 1,
                action='approved',
                reviewer_id=officer.id,
                reviewed_at=base + timedelta(minutes=i),
                comments=f'Assignment Review {i}',
            ))
        db.session.commit()
        assignment_id = assignment.id

    resp = client.get(f'/samples/assignment/{assignment_id}')
    assert resp.status_code == 200
    assert b'Assignment Review 11' in resp.data
    assert b'Assignment Review 0' not in resp.data
    assert b'review_page=2' in resp.data

    resp = client.get(f'/samples/assignment/{assignment_id}?review_page=2')
    assert resp.status_code == 200
    assert b'Assignment Review 0' in resp.data
    assert b'Assignment Review 11' not in resp.data


def test_assign_sample(app, client):
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Screening Test',
        'test_reference': 'REF-001',
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'assigned successfully' in resp.data


def test_submit_report(app, client):
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    # Assign
    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Blood Analysis',
    }, follow_redirects=True)
    client.get('/auth/logout')

    # Submit report
    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'No harmful substances detected.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert resp.status_code == 200
    assert b'submitted successfully' in resp.data

    # Verify assignment is now REPORT_SUBMITTED (awaiting preliminary review)
    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.REPORT_SUBMITTED


def test_preliminary_review(app, client):
    """Test that Officer can do preliminary review after analyst submits."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Test results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Officer does preliminary review
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'Complete and well-documented.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'approved and forwarded' in resp.data

    # Verify assignment is now UNDER_TECHNICAL_REVIEW
    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.UNDER_TECHNICAL_REVIEW


def test_preliminary_review_return(app, client):
    """Test that Officer can return report during preliminary review."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Incomplete.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Officer returns for correction
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'returned',
        'review_comments': 'Missing sections.',
    }, follow_redirects=True)
    assert b'returned for correction' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.RETURNED
        assert assignment.return_stage == 'preliminary'

    # Chemist resubmits → goes back to REPORT_SUBMITTED (preliminary)
    client.get('/auth/logout')
    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Complete findings with all sections.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert b'submitted successfully' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.REPORT_SUBMITTED


def test_my_preliminary_reviews_page(app, client):
    """Officer can view all preliminary reviews they've completed for every
    analyst, including a returned review and the subsequent re-review after
    resubmission."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Incomplete.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Officer returns for correction (first preliminary review)
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'returned',
        'review_comments': 'Missing sections.',
    }, follow_redirects=True)
    client.get('/auth/logout')

    # Chemist resubmits
    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Complete findings with all sections.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    client.get('/auth/logout')

    # Officer approves on the second pass (preliminary review after resubmission)
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'Now complete.',
    }, follow_redirects=True)

    resp = client.get('/samples/preliminary-reviews/mine')
    assert resp.status_code == 200
    assert b'My Preliminary Reviews' in resp.data
    # Both the returned review and the subsequent approved review show up
    assert b'Returned' in resp.data
    assert b'Approved' in resp.data
    assert b'Resubmission' in resp.data
    assert b'Chemist User' in resp.data or b'chemist' in resp.data.lower()

    # Filtering by analyst still returns the same reviews
    with app.app_context():
        chemist = db.session.get(User, chemist_id)
    resp = client.get(f'/samples/preliminary-reviews/mine?analyst_id={chemist.id}')
    assert resp.status_code == 200
    assert b'Returned' in resp.data
    assert b'Approved' in resp.data

    # A user without preliminary-review privileges cannot access the page
    client.get('/auth/logout')
    _login(client, 'chemist')
    resp = client.get('/samples/preliminary-reviews/mine', follow_redirects=True)
    assert b'do not have permission' in resp.data


def test_my_preliminary_reviews_permission_scopes(app, client):
    """Permission-based visibility: 'own' scope users cannot see another
    reviewer's history (even via URL/param manipulation), 'team' scope users
    only see reviewers who share a branch, and 'all' scope users see every
    reviewer plus the "Assigned To" column, with an audit log entry written
    when viewing others' reviews."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    with app.app_context():
        # A second reviewer in a different branch (no shared branch with the
        # senior chemist / officer) to prove 'team' scope excludes them.
        other_officer = _create_user(Role.OFFICER, Branch.FOOD_MILK, username='officer2')
        other_officer_id = other_officer.id

        sample1 = Sample(
            lab_number='TOX-100', sample_name='Sample One',
            sample_type=Branch.TOXICOLOGY, date_received=date.today(),
            uploaded_by=officer_id,
        )
        sample2 = Sample(
            lab_number='MLK-200', sample_name='Sample Two',
            sample_type=Branch.FOOD_MILK, date_received=date.today(),
            uploaded_by=other_officer_id,
        )
        db.session.add_all([sample1, sample2])
        db.session.flush()

        assignment1 = SampleAssignment(
            sample_id=sample1.id, chemist_id=chemist_id,
            assigned_by=officer_id, test_name='Test One',
        )
        assignment2 = SampleAssignment(
            sample_id=sample2.id, chemist_id=chemist_id,
            assigned_by=other_officer_id, test_name='Test Two',
        )
        db.session.add_all([assignment1, assignment2])
        db.session.flush()

        # Officer (senior-chemist's teammate via TOXICOLOGY branch) reviews sample1.
        db.session.add(ReviewHistory(
            sample_id=sample1.id, assignment_id=assignment1.id,
            review_type='preliminary', review_number=1, action='approved',
            reviewer_id=sc_id, comments='SC review',
        ))
        # A different-branch officer reviews sample2 — outside the SC's team.
        db.session.add(ReviewHistory(
            sample_id=sample2.id, assignment_id=assignment2.id,
            review_type='preliminary', review_number=1, action='approved',
            reviewer_id=other_officer_id, comments='Officer2 review',
        ))
        db.session.commit()

        sc_user = db.session.get(User, sc_id)
        officer2_user = db.session.get(User, other_officer_id)
        admin_user = _create_user(Role.ADMIN, username='admin_user')
        admin_id = admin_user.id

        assert AuditLog.query.count() == 0

    # --- 'own' scope: officer2 sees only their own review, and cannot use
    # the assigned_to param to view the senior chemist's review.
    _login(client, 'officer2')
    resp = client.get('/samples/preliminary-reviews/mine')
    assert resp.status_code == 200
    assert b'Officer2 review' in resp.data
    assert b'SC review' not in resp.data

    resp = client.get(f'/samples/preliminary-reviews/mine?assigned_to={sc_id}')
    assert resp.status_code == 200
    assert b'SC review' not in resp.data
    assert b'do not have permission to view that reviewer' in resp.data
    client.get('/auth/logout')

    # --- 'team' scope: grant VIEW_TEAM_PRELIMINARY_REVIEWS to the senior
    # chemist. They should see their own review but not officer2's (different
    # branch), and the "Assigned To" filter/column becomes available.
    with app.app_context():
        db.session.execute(user_permissions.insert().values(
            user_id=sc_id, permission=Permission.VIEW_TEAM_PRELIMINARY_REVIEWS,
        ))
        db.session.commit()

    _login(client, 'senior')
    resp = client.get('/samples/preliminary-reviews/mine')
    assert resp.status_code == 200
    assert b'SC review' in resp.data
    assert b'Officer2 review' not in resp.data
    assert b'Assigned To' in resp.data

    resp = client.get(f'/samples/preliminary-reviews/mine?assigned_to={other_officer_id}')
    assert resp.status_code == 200
    assert b'Officer2 review' not in resp.data
    assert b'do not have permission to view that reviewer' in resp.data
    client.get('/auth/logout')

    # --- 'all' scope: an admin should see every reviewer's reviews, along
    # with the "Assigned To" column, and viewing others triggers an audit
    # log entry.
    with app.app_context():
        audit_count_before = AuditLog.query.count()

    _login(client, 'admin_user')
    resp = client.get('/samples/preliminary-reviews/mine')
    assert resp.status_code == 200
    assert b'SC review' in resp.data
    assert b'Officer2 review' in resp.data
    assert b'Assigned To' in resp.data

    with app.app_context():
        assert AuditLog.query.count() > audit_count_before
        entry = AuditLog.query.filter_by(
            action='VIEW_OTHERS_PRELIMINARY_REVIEWS'
        ).order_by(AuditLog.id.desc()).first()
        assert entry is not None
        assert entry.performed_by == admin_id

    # Filtering to a specific reviewer within 'all' scope works and further
    # narrows the audit trail's entity reference.
    resp = client.get(f'/samples/preliminary-reviews/mine?assigned_to={other_officer_id}')
    assert resp.status_code == 200
    assert b'Officer2 review' in resp.data
    assert b'SC review' not in resp.data


def test_preliminary_review_checklist(app, client):
    """Test that preliminary review checklist items are saved."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Test results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Officer does preliminary review with checklist items
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'All checks passed.',
        'chk_original_entry_visible': 'yes',
        'chk_entries_signed': 'yes',
        'chk_date_recorded': 'yes',
        'chk_conclusions_signed_dated': 'yes',
        'chk_report_signed_dated': 'yes',
        'chk_printouts_attached': 'yes',
        'chk_attachments_labeled': 'yes',
        'chk_analyst_initials': 'yes',
        'chk_templates_completed': 'yes',
        'chk_writing_legible': 'yes',
        'chk_logbooks_updated': 'yes',
        'chk_toc_updated': 'yes',
        'chk_pages_numbered': 'yes',
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'approved and forwarded' in resp.data

    # Verify checklist was saved
    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.preliminary_review_checklist is not None
        checklist = json.loads(assignment.preliminary_review_checklist)
        assert checklist['chk_original_entry_visible'] == 'yes'
        assert checklist['chk_entries_signed'] == 'yes'


def test_preliminary_review_checklist_partial(app, client):
    """Test that unchecked checklist items are saved as False."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Test results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Officer does preliminary review with only some checklist items checked
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'returned',
        'review_comments': 'Missing signatures.',
        'chk_original_entry_visible': 'yes',
        'chk_entries_signed': 'yes',
        'chk_date_recorded': 'na',
        'chk_conclusions_signed_dated': 'na',
        'chk_report_signed_dated': 'na',
        'chk_printouts_attached': 'na',
        'chk_attachments_labeled': 'na',
        'chk_analyst_initials': 'na',
        'chk_templates_completed': 'na',
        'chk_writing_legible': 'na',
        'chk_logbooks_updated': 'na',
        'chk_toc_updated': 'na',
        'chk_pages_numbered': 'na',
    }, follow_redirects=True)
    assert b'returned for correction' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        checklist = json.loads(assignment.preliminary_review_checklist)
        assert checklist['chk_original_entry_visible'] == 'yes'
        assert checklist['chk_entries_signed'] == 'yes'
        assert checklist['chk_date_recorded'] == 'na'


def test_returned_resubmission_only_updates_target_assignment(app, client):
    """Resubmitting a returned report must not update other assignments."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Returned Test',
    }, follow_redirects=True)
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Unaffected Test',
    }, follow_redirects=True)
    client.get('/auth/logout')

    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        returned_assignment, unaffected_assignment = assignments
        returned_assignment.status = AssignmentStatus.RETURNED
        returned_assignment.return_stage = 'preliminary'
        unaffected_assignment.status = AssignmentStatus.ASSIGNED
        db.session.commit()
        returned_id = returned_assignment.id
        unaffected_id = unaffected_assignment.id

    _login(client, 'chemist')
    resp = client.post(f'/samples/assignment/{returned_id}/report', data={
        'report_text': 'Corrected report for returned test only.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert resp.status_code == 200
    assert b'submitted successfully' in resp.data

    with app.app_context():
        returned_assignment = db.session.get(SampleAssignment, returned_id)
        unaffected_assignment = db.session.get(SampleAssignment, unaffected_id)
        assert returned_assignment.status == AssignmentStatus.REPORT_SUBMITTED
        assert returned_assignment.return_stage is None
        assert unaffected_assignment.status == AssignmentStatus.ASSIGNED
        assert unaffected_assignment.report_submitted_at is None
        assert unaffected_assignment.report_text is None


def test_technical_review(app, client):
    """Test full flow: submit → preliminary approve → technical accept."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Purity Test',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Results within acceptable range.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Preliminary review
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'OK',
    })
    client.get('/auth/logout')

    # Technical review
    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted',
        'review_comments': 'Looks good.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'accepted' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.ACCEPTED
        sample = Sample.query.first()
        assert sample.status == SampleStatus.ACCEPTED


def test_preliminary_grouped_return_only_returns_selected_assignment(app, client):
    """Grouped preliminary return must only return the selected report."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test A',
    }, follow_redirects=True)
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test B',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        first_assignment_id = assignments[0].id
    client.post(f'/samples/assignment/{first_assignment_id}/report', data={
        'report_text': 'Initial grouped report.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    client.get('/auth/logout')

    with app.app_context():
        db.session.commit()

    _login(client, 'officer')
    resp = client.post(f'/samples/assignment/{first_assignment_id}/preliminary-review', data={
        'action': 'returned',
        'review_comments': 'Only Test A needs correction.',
    }, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        assert assignments[0].status == AssignmentStatus.RETURNED
        assert assignments[1].status == AssignmentStatus.REPORT_SUBMITTED


def test_preliminary_grouped_return_can_return_all_assignments(app, client):
    """Grouped preliminary return can return all reports when explicitly selected."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test A',
    }, follow_redirects=True)
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test B',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        first_assignment_id = assignments[0].id
    client.post(f'/samples/assignment/{first_assignment_id}/report', data={
        'report_text': 'Initial grouped report.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'officer')
    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        all_ids = [a.id for a in assignments]
    resp = client.post(f'/samples/assignment/{first_assignment_id}/preliminary-review', data={
        'action': 'returned',
        'return_scope': 'all',
        'assignment_ids': all_ids,
    }, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        assert assignments[0].status == AssignmentStatus.RETURNED
        assert assignments[1].status == AssignmentStatus.RETURNED


def test_preliminary_review_approve_multiple_reports(app, client):
    """Preliminary review can approve multiple reports at once without grouped setting."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test A',
    }, follow_redirects=True)
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test B',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        first_assignment_id = assignments[0].id
    client.post(f'/samples/assignment/{first_assignment_id}/report', data={
        'report_text': 'Combined report.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    client.get('/auth/logout')

    # Officer approves both reports without needing grouped setting
    _login(client, 'officer')
    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        all_ids = [a.id for a in assignments]
    resp = client.post(f'/samples/assignment/{first_assignment_id}/preliminary-review', data={
        'action': 'approved',
        'assignment_ids': all_ids,
        'review_comments': 'Both tests look good.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'2 reports have been approved and forwarded' in resp.data

    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        assert assignments[0].status == AssignmentStatus.UNDER_TECHNICAL_REVIEW
        assert assignments[1].status == AssignmentStatus.UNDER_TECHNICAL_REVIEW


def test_technical_review_return(app, client):
    """Test technical review return → analyst resubmits → goes to tech review."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Quality Check',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Initial findings.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Preliminary approve
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
    })
    client.get('/auth/logout')

    # Technical review → return
    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'returned',
        'review_comments': 'Please add more detail.',
    }, follow_redirects=True)
    assert b'returned' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.return_stage == 'technical'

    # Chemist resubmits → goes directly to technical review (skips preliminary)
    client.get('/auth/logout')
    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Updated detailed findings.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert b'submitted successfully' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.UNDER_TECHNICAL_REVIEW
        assert assignment.return_stage is None


def test_technical_grouped_return_only_returns_selected_assignment(app, client):
    """Grouped technical return must only return the selected report."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Tech Test A',
    }, follow_redirects=True)
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Tech Test B',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        first_assignment_id = assignments[0].id
        second_assignment_id = assignments[1].id
    client.post(f'/samples/assignment/{first_assignment_id}/report', data={
        'report_text': 'Initial report for both tests.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'officer')
    client.post(f'/samples/assignment/{first_assignment_id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'A ok',
    }, follow_redirects=True)
    client.post(f'/samples/assignment/{second_assignment_id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'B ok',
    }, follow_redirects=True)
    client.get('/auth/logout')

    with app.app_context():
        Setting.set('technical_review_grouped', 'true')
        db.session.commit()

    _login(client, 'senior')
    resp = client.post(f'/samples/assignment/{first_assignment_id}/review', data={
        'action': 'returned',
        'review_comments': 'Only Test A needs revision.',
    }, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        assert assignments[0].status == AssignmentStatus.RETURNED
        assert assignments[0].return_stage == 'technical'
        assert assignments[1].status == AssignmentStatus.UNDER_TECHNICAL_REVIEW
        Setting.set('technical_review_grouped', 'false')
        db.session.commit()


def test_technical_grouped_return_can_return_second_selected_assignment(app, client):
    """Grouped technical return should work for any selected report, not just the first."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Tech Test A',
    }, follow_redirects=True)
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Tech Test B',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        first_assignment_id = assignments[0].id
        second_assignment_id = assignments[1].id
    client.post(f'/samples/assignment/{first_assignment_id}/report', data={
        'report_text': 'Initial report for both tests.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'officer')
    client.post(f'/samples/assignment/{first_assignment_id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'A ok',
    }, follow_redirects=True)
    client.post(f'/samples/assignment/{second_assignment_id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'B ok',
    }, follow_redirects=True)
    client.get('/auth/logout')

    with app.app_context():
        Setting.set('technical_review_grouped', 'true')
        db.session.commit()

    _login(client, 'senior')
    resp = client.post(f'/samples/assignment/{second_assignment_id}/review', data={
        'action': 'returned',
        'review_comments': 'Only Test B needs revision.',
    }, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        assert assignments[0].status == AssignmentStatus.UNDER_TECHNICAL_REVIEW
        assert assignments[1].status == AssignmentStatus.RETURNED
        assert assignments[1].return_stage == 'technical'
        Setting.set('technical_review_grouped', 'false')
        db.session.commit()


def test_technical_grouped_return_can_return_all_assignments(app, client):
    """Grouped technical return can return all reports when explicitly selected."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Tech Test A',
    }, follow_redirects=True)
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Tech Test B',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
        first_assignment_id = assignments[0].id
        second_assignment_id = assignments[1].id
    client.post(f'/samples/assignment/{first_assignment_id}/report', data={
        'report_text': 'Initial report for both tests.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data', follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'officer')
    client.post(f'/samples/assignment/{first_assignment_id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'A ok',
    }, follow_redirects=True)
    client.post(f'/samples/assignment/{second_assignment_id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'B ok',
    }, follow_redirects=True)
    client.get('/auth/logout')

    with app.app_context():
        Setting.set('technical_review_grouped', 'true')
        db.session.commit()

    try:
        _login(client, 'senior')
        resp = client.post(f'/samples/assignment/{first_assignment_id}/review', data={
            'action': 'returned',
            'return_scope': 'all',
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            assignments = SampleAssignment.query.order_by(SampleAssignment.id).all()
            assert assignments[0].status == AssignmentStatus.RETURNED
            assert assignments[1].status == AssignmentStatus.RETURNED
    finally:
        with app.app_context():
            Setting.set('technical_review_grouped', 'false')
            db.session.commit()


def test_full_workflow(app, client):
    """Test the complete 26-step workflow end to end."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    # 1. Register sample
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    # 2. Assign
    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Full Analysis',
    })
    client.get('/auth/logout')

    # 3. Submit report
    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Comprehensive analysis results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # 4. Preliminary review (Officer)
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
    })
    client.get('/auth/logout')

    # 5. Technical review (Senior Chemist)
    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted',
        'review_comments': 'Excellent work.',
    })

    # 6. Submit to Deputy
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/submit-to-deputy', data={},
                       follow_redirects=True)
    assert b'submitted to Deputy' in resp.data
    client.get('/auth/logout')

    # 7. Deputy review
    _login(client, 'deputy')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/deputy-review', data={
        'action': 'approved',
        'review_comments': 'All in order.',
    }, follow_redirects=True)
    assert b'approved' in resp.data
    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.CERTIFICATE_PREPARATION

    # 8. Prepare certificate (Deputy)
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/prepare-certificate', data={
        'certificate_text': 'This certifies the sample has been analysed.',
    }, follow_redirects=True)
    assert b'Certificate of Analysis submitted' in resp.data
    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.HOD_REVIEW
    client.get('/auth/logout')

    # 9. HOD signs certificate
    _login(client, 'hod')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/hod-review', data={
        'action': 'sign',
        'review_comments': 'Verified.',
    }, follow_redirects=True)
    assert b'Certificate of Analysis signed' in resp.data

    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.CERTIFIED
        assert sample.certified_by == hod_id


def test_full_workflow_pharma_with_summary(app, client):
    """Test pharmaceutical workflow requires summary report."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    # Register pharma sample
    _login(client, 'officer')
    client.post('/samples/register', data={
        'lab_number': 'PHARMA-001',
        'sample_name': 'Test Drug',
        'sample_type': 'PHARMACEUTICAL',
        'date_received': '2026-01-15',
    }, follow_redirects=True)
    client.get('/auth/logout')

    # Assign (need a pharma senior chemist)
    with app.app_context():
        sc_pharma = _create_user(
            Role.SENIOR_CHEMIST, Branch.PHARMACEUTICAL, username='sc_pharma'
        )
        chem_pharma = _create_user(
            Role.CHEMIST, Branch.PHARMACEUTICAL, username='chem_pharma'
        )
        sc_pharma_id = sc_pharma.id
        chem_pharma_id = chem_pharma.id

    _login(client, 'sc_pharma')
    with app.app_context():
        sample = Sample.query.filter_by(lab_number='PHARMA-001').first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chem_pharma_id],
        'test_name': 'Drug Purity',
    })
    client.get('/auth/logout')

    # Submit report
    _login(client, 'chem_pharma')
    with app.app_context():
        assignment = SampleAssignment.query.filter_by(
            sample_id=sample.id
        ).first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Drug meets purity standards.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Preliminary review
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.filter_by(
            sample_id=sample.id
        ).first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
    })
    client.get('/auth/logout')

    # Technical review
    _login(client, 'sc_pharma')
    with app.app_context():
        assignment = SampleAssignment.query.filter_by(
            sample_id=sample.id
        ).first()
    client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted',
    })

    # Submit to Deputy WITH summary (required for pharma)
    with app.app_context():
        sample = Sample.query.filter_by(lab_number='PHARMA-001').first()

    # First try without summary → should fail
    resp = client.post(f'/samples/{sample.id}/submit-to-deputy', data={},
                       follow_redirects=True)
    assert b'Summary report is required' in resp.data

    # Now with summary
    resp = client.post(f'/samples/{sample.id}/submit-to-deputy', data={
        'summary_report': 'Summary: Drug meets all pharma standards.',
    }, follow_redirects=True)
    assert b'submitted to Deputy' in resp.data

    with app.app_context():
        sample = Sample.query.filter_by(lab_number='PHARMA-001').first()
        assert sample.summary_report is not None


def test_submit_to_deputy_after_correction_when_sample_status_stale(app, client):
    """Senior chemist can still submit to Deputy after correction loop if sample status is stale."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Deputy Submission with Stale Status',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Initial report.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
    })
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted',
    })

    with app.app_context():
        sample = Sample.query.first()
        assignment = SampleAssignment.query.first()
        # Simulate stale sample status while assignment is already accepted.
        sample.status = SampleStatus.RETURNED
        assignment.status = AssignmentStatus.ACCEPTED
        db.session.commit()
        sample_id = sample.id

    resp = client.get(f'/samples/{sample_id}')
    assert b'Submit to Deputy' in resp.data

    resp = client.post(f'/samples/{sample_id}/submit-to-deputy', data={},
                       follow_redirects=True)
    assert b'submitted to Deputy' in resp.data

    with app.app_context():
        sample = db.session.get(Sample, sample_id)
        assert sample.status == SampleStatus.DEPUTY_REVIEW


def test_deputy_return(app, client):
    """Test that Deputy can return submission to Senior Chemist."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
    })
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted',
    })
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/submit-to-deputy', data={})
    client.get('/auth/logout')

    # Deputy returns
    _login(client, 'deputy')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/deputy-review', data={
        'action': 'returned',
        'review_comments': 'Needs clarification.',
    }, follow_redirects=True)
    assert b'returned to Senior Chemist' in resp.data

    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.DEPUTY_RETURNED

    # Senior Chemist resubmits
    client.get('/auth/logout')
    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/resubmit-to-deputy',
                       follow_redirects=True)
    assert b'Resubmitted to Deputy' in resp.data

    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.DEPUTY_REVIEW


def test_deputy_reject(app, client):
    """Test that Deputy can no longer reject submission (option removed)."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
    })
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted',
    })
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/submit-to-deputy', data={})
    client.get('/auth/logout')

    # Deputy submits 'rejected' — should be rejected by form validation
    _login(client, 'deputy')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/deputy-review', data={
        'action': 'rejected',
        'review_comments': 'Not acceptable.',
    }, follow_redirects=True)
    # The form should fail validation (rejected is not a valid choice)
    with app.app_context():
        sample = Sample.query.first()
        assert sample.status != SampleStatus.REJECTED


def test_admin_unreject_sample(app, client):
    """Test that Admin can remove rejected status and return sample to Deputy Review."""
    # Create all users in a single app context (including admin)
    with app.app_context():
        _create_user(Role.OFFICER, username='officer')
        _create_user(Role.SENIOR_CHEMIST, Branch.TOXICOLOGY, username='senior')
        _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chemist')
        _create_user(Role.DEPUTY, username='deputy')
        _create_user(Role.HOD, username='hod')
        _create_user(Role.ADMIN, username='admin')

    # Register sample as officer
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    # Force sample to REJECTED status directly in DB
    with app.app_context():
        sample = Sample.query.first()
        sample.status = SampleStatus.REJECTED
        db.session.commit()
        sample_id = sample.id

    # Non-admin cannot unreject
    _login(client, 'deputy')
    client.post(f'/samples/{sample_id}/unreject', data={
        'unreject_reason': 'Restore this sample',
    }, follow_redirects=True)
    with app.app_context():
        sample = db.session.get(Sample, sample_id)
        assert sample.status == SampleStatus.REJECTED
    client.get('/auth/logout')

    # Admin can unreject
    _login(client, 'admin')
    resp = client.post(f'/samples/{sample_id}/unreject', data={
        'unreject_reason': 'Restore this sample',
    }, follow_redirects=True)
    assert b'Rejected status has been removed' in resp.data
    with app.app_context():
        sample = db.session.get(Sample, sample_id)
        assert sample.status == SampleStatus.DEPUTY_REVIEW


def test_deputy_return_resubmit_via_form(app, client):
    """Test that lab supervisor can resubmit to deputy via the submit_to_deputy form
    after the deputy returns the submission (DEPUTY_RETURNED status)."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
    })
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted',
    })
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/submit-to-deputy', data={})
    client.get('/auth/logout')

    # Deputy returns
    _login(client, 'deputy')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/deputy-review', data={
        'action': 'returned',
        'review_comments': 'Needs clarification.',
    })
    client.get('/auth/logout')

    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.DEPUTY_RETURNED

    # Senior Chemist resubmits via the submit_to_deputy form (GET should work)
    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.get(f'/samples/{sample.id}/submit-to-deputy')
    assert resp.status_code == 200

    # POST the form to resubmit
    resp = client.post(f'/samples/{sample.id}/submit-to-deputy', data={},
                       follow_redirects=True)
    assert b'Resubmitted to Deputy' in resp.data

    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.DEPUTY_REVIEW


def test_hod_return_certificate(app, client):
    """Test that HOD can return certificate to Deputy."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
    })
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted',
    })
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/submit-to-deputy', data={})
    client.get('/auth/logout')

    _login(client, 'deputy')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/deputy-review', data={
        'action': 'approved',
    })
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/prepare-certificate', data={
        'certificate_text': 'Certificate of Analysis.',
    })
    client.get('/auth/logout')

    # HOD returns certificate
    _login(client, 'hod')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/hod-review', data={
        'action': 'returned',
        'review_comments': 'Fix formatting.',
    }, follow_redirects=True)
    assert b'returned to Deputy' in resp.data

    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.HOD_RETURNED

    # Deputy revises certificate
    client.get('/auth/logout')
    _login(client, 'deputy')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/prepare-certificate', data={
        'certificate_text': 'Revised Certificate of Analysis.',
    }, follow_redirects=True)
    assert b'Certificate of Analysis submitted' in resp.data

    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.HOD_REVIEW


def test_chemist_cannot_register_sample(app, client):
    _setup_users(app)
    _login(client, 'chemist')
    resp = client.get('/samples/register', follow_redirects=True)
    assert b'Only officers can register samples' in resp.data


def test_officer_cannot_assign(app, client):
    _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    with app.app_context():
        sample = Sample.query.first()
    resp = client.get(f'/samples/{sample.id}/assign', follow_redirects=True)
    assert b'Senior Chemists' in resp.data


def test_remove_assignment_by_admin(app, client):
    """Test that Admin can remove an assignment."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test to Remove',
    }, follow_redirects=True)
    client.get('/auth/logout')

    # Admin removes the assignment
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin')
    _login(client, 'admin')
    with app.app_context():
        assignment = SampleAssignment.query.first()
        assignment_id = assignment.id
    resp = client.post(f'/samples/assignment/{assignment_id}/remove',
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b'has been removed' in resp.data

    # Sample should revert to REGISTERED since no assignments left
    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.REGISTERED
        assert SampleAssignment.query.count() == 0


def test_remove_assignment_by_assigner(app, client):
    """Test that the user who assigned can remove an assignment."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test to Remove',
    }, follow_redirects=True)

    # Same senior chemist removes the assignment
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/remove',
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b'has been removed' in resp.data


def test_remove_assignment_denied_for_chemist(app, client):
    """Test that a regular chemist cannot remove assignments."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/remove',
                       follow_redirects=True)
    assert b'do not have permission' in resp.data


def test_remove_assignment_audited(app, client):
    """Test that assignment removal is recorded in history."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Audit Test',
    }, follow_redirects=True)

    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/remove',
                follow_redirects=True)

    with app.app_context():
        from app.models import SampleHistory
        history = SampleHistory.query.filter_by(action='Assignment Removed').first()
        assert history is not None
        assert 'Audit Test' in history.details


def test_senior_chemist_can_do_preliminary_review(app, client):
    """Test that Senior Chemist can perform preliminary review."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'SC Prelim Test',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Test results for SC review.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Senior Chemist does preliminary review (not just Officer)
    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'Senior Chemist preliminary approval.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'approved and forwarded' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.UNDER_TECHNICAL_REVIEW


def test_submit_report_with_return_fields(app, client):
    """Test that report submission saves all_samples_returned and return_quantity."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Return Test',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Test report with return info.',
        'report_file': _report_file(),
        'all_samples_returned': 'Yes',
        'return_quantity': '45ml',
    }, content_type='multipart/form-data', follow_redirects=True)
    assert resp.status_code == 200
    assert b'submitted successfully' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.all_samples_returned == 'Yes'
        assert assignment.return_quantity == '45ml'


def test_assign_with_comments_and_quantity(app, client):
    """Test that assignment saves comments and quantity_volume."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    resp = client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
        'comments': 'Handle with care, priority sample.',
        'quantity_volume': '100ml',
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'assigned successfully' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.comments == 'Handle with care, priority sample.'
        assert assignment.quantity_volume == '100ml'


# ---------------------------------------------------------------------------
# Bulk Delete Tests
# ---------------------------------------------------------------------------

def _create_admin(app):
    """Create and return an admin user."""
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin')
        return admin.id


def _register_sample_with_lab(client, lab, name='Test Sample'):
    """Register a sample with a specific lab number."""
    return client.post('/samples/register', data={
        'lab_number': lab,
        'sample_name': name,
        'sample_type': 'TOXICOLOGY',
        'date_received': '2026-01-15',
        'description': 'Test',
        'quantity': '50ml',
    }, follow_redirects=True)


def test_bulk_delete_admin_deletes_samples(app, client):
    """Admin can bulk-delete samples and related data is removed."""
    _setup_users(app)
    _create_admin(app)

    # Register two samples as officer
    _login(client, 'officer')
    _register_sample_with_lab(client, 'TOX-001')
    _register_sample_with_lab(client, 'TOX-002')
    client.get('/auth/logout')

    _login(client, 'admin')
    with app.app_context():
        samples = Sample.query.all()
        assert len(samples) == 2
        ids = [s.id for s in samples]

    resp = client.post('/samples/bulk-delete', data={
        'sample_ids': ids,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'2 samples deleted' in resp.data

    with app.app_context():
        assert Sample.query.count() == 0


def test_bulk_delete_creates_audit_log(app, client):
    """Every bulk deletion creates a permanent AuditLog entry."""
    from app.models import AuditLog

    _setup_users(app)
    _create_admin(app)

    _login(client, 'officer')
    _register_sample_with_lab(client, 'TOX-AUDIT')
    client.get('/auth/logout')

    _login(client, 'admin')
    with app.app_context():
        sample = Sample.query.first()
        sample_id = sample.id

    client.post('/samples/bulk-delete', data={
        'sample_ids': [sample_id],
    }, follow_redirects=True)

    with app.app_context():
        # Sample is gone
        assert Sample.query.count() == 0
        # Audit log entry exists
        log = AuditLog.query.filter_by(action='SAMPLE_DELETED').first()
        assert log is not None
        assert log.action == 'SAMPLE_DELETED'
        assert log.entity_type == 'Sample'
        assert log.entity_label == 'TOX-AUDIT'
        assert log.entity_id == sample_id
        assert '"lab_number": "TOX-AUDIT"' in log.details


def test_bulk_delete_denied_for_non_admin(app, client):
    """Non-admin users cannot bulk-delete samples."""
    _setup_users(app)
    _login(client, 'officer')
    _register_sample_with_lab(client, 'TOX-001')

    with app.app_context():
        sample = Sample.query.first()
        sample_id = sample.id

    resp = client.post('/samples/bulk-delete', data={
        'sample_ids': [sample_id],
    }, follow_redirects=True)
    assert b'Access denied' in resp.data

    with app.app_context():
        assert Sample.query.count() == 1


def test_bulk_delete_no_selection(app, client):
    """Submitting with no sample_ids flashes a warning."""
    _create_admin(app)
    _login(client, 'admin')

    resp = client.post('/samples/bulk-delete', data={}, follow_redirects=True)
    assert b'No samples selected' in resp.data


def test_bulk_delete_cascades_assignments(app, client):
    """Assignments are also deleted when the parent sample is bulk-deleted."""
    from app.models import AuditLog

    officer_id, sc_id, chemist_id, _, _ = _setup_users(app)
    _create_admin(app)

    _login(client, 'officer')
    _register_sample_with_lab(client, 'TOX-CASCADE')
    client.get('/auth/logout')

    # Assign a chemist
    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Quality',
    })
    client.get('/auth/logout')

    with app.app_context():
        assert SampleAssignment.query.count() == 1

    _login(client, 'admin')
    client.post('/samples/bulk-delete', data={
        'sample_ids': [sample.id],
    }, follow_redirects=True)

    with app.app_context():
        assert Sample.query.count() == 0
        assert SampleAssignment.query.count() == 0
        # Audit log captures assignment count
        log = AuditLog.query.filter_by(action='SAMPLE_DELETED').first()
        assert log is not None
        assert '"assignment_count": 1' in log.details


def test_bulk_delete_checkboxes_visible_for_admin(app, client):
    """The sample list page shows checkboxes only for admin users."""
    _setup_users(app)
    _create_admin(app)

    _login(client, 'officer')
    _register_sample_with_lab(client, 'TOX-VIS')
    client.get('/auth/logout')

    # Non-admin sees no checkboxes
    _login(client, 'officer')
    resp = client.get('/samples/')
    assert b'id="select-all-samples"' not in resp.data
    assert b'class="form-check-input sample-checkbox"' not in resp.data
    client.get('/auth/logout')

    # Admin sees checkboxes
    _login(client, 'admin')
    resp = client.get('/samples/')
    assert b'id="select-all-samples"' in resp.data
    assert b'class="form-check-input sample-checkbox"' in resp.data


# ---------------------------------------------------------------------------
# Duplicate assignment prevention
# ---------------------------------------------------------------------------

def test_assign_duplicate_skipped(app, client):
    """Assigning the same chemist+test again should skip duplicates."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()

    # First assignment
    resp = client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    }, follow_redirects=True)
    assert b'assigned successfully' in resp.data

    with app.app_context():
        count = SampleAssignment.query.filter_by(sample_id=sample.id).count()
        assert count == 1

    # Duplicate assignment attempt
    resp = client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    }, follow_redirects=True)
    assert b'already assigned' in resp.data

    with app.app_context():
        count = SampleAssignment.query.filter_by(sample_id=sample.id).count()
        assert count == 1  # Still only one assignment


def test_assign_different_test_allowed(app, client):
    """Assigning a different test to the same chemist should work."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()

    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis A',
    }, follow_redirects=True)

    resp = client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis B',
    }, follow_redirects=True)
    assert b'assigned successfully' in resp.data

    with app.app_context():
        count = SampleAssignment.query.filter_by(sample_id=sample.id).count()
        assert count == 2


# ---------------------------------------------------------------------------
# Preliminary review – uploader access
# ---------------------------------------------------------------------------

def test_uploader_can_do_preliminary_review(app, client):
    """The user who uploaded the sample can do preliminary review
    even if the template allows it via uploaded_by check."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Test',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    # Officer (the uploader) does preliminary review
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved',
        'review_comments': 'Looks good.',
        'chk_original_entry_visible': 'yes',
        'chk_entries_signed': 'yes',
        'chk_date_recorded': 'yes',
        'chk_conclusions_signed_dated': 'yes',
        'chk_report_signed_dated': 'yes',
        'chk_printouts_attached': 'yes',
        'chk_attachments_labeled': 'yes',
        'chk_analyst_initials': 'yes',
        'chk_templates_completed': 'yes',
        'chk_writing_legible': 'yes',
        'chk_logbooks_updated': 'yes',
        'chk_toc_updated': 'yes',
        'chk_pages_numbered': 'yes',
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'approved and forwarded' in resp.data


# ---------------------------------------------------------------------------
# Backdate request notifications
# ---------------------------------------------------------------------------

def test_backdate_request_notifies_hod_deputy(app, client):
    """Submitting a backdate request should create notifications for HOD/Deputy."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    _login(client, 'officer')
    _register_sample(client)

    with app.app_context():
        sample = Sample.query.first()
        from app.models import Notification
        initial_count = Notification.query.count()

    resp = client.post(f'/samples/{sample.id}/request-backdate', data={
        'field_name': 'date_registered',
        'assignment_id': '0',
        'proposed_date': '2025-12-01',
        'reason': 'Sample arrived earlier than recorded.',
    }, follow_redirects=True)
    assert b'submitted for approval' in resp.data

    with app.app_context():
        from app.models import Notification
        new_notifs = Notification.query.filter(
            Notification.title.contains('Back-Date Request')
        ).all()
        # Should have notifications for both HOD and Deputy
        notified_user_ids = {n.user_id for n in new_notifs}
        assert deputy_id in notified_user_ids
        assert hod_id in notified_user_ids


def test_backdate_decision_notifies_requester(app, client):
    """Approving/denying a backdate request should notify the requester."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    _login(client, 'officer')
    _register_sample(client)

    with app.app_context():
        sample = Sample.query.first()

    client.post(f'/samples/{sample.id}/request-backdate', data={
        'field_name': 'date_registered',
        'assignment_id': '0',
        'proposed_date': '2025-12-01',
        'reason': 'Earlier arrival.',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'hod')
    with app.app_context():
        from app.models import BackDateRequest, Notification
        bdr = BackDateRequest.query.first()
        initial_count = Notification.query.filter_by(user_id=officer_id).count()

    resp = client.post(f'/backdate-requests/{bdr.id}/decide', data={
        'decision': 'approved',
        'comments': 'Approved.',
    }, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        from app.models import Notification
        notifs = Notification.query.filter(
            Notification.user_id == officer_id,
            Notification.title.contains('Back-Date Request Approved'),
        ).all()
        assert len(notifs) >= 1


def test_backdate_request_supports_new_review_and_reissue_fields(app, client):
    """Back-date requests should support senior review, deputy review, and certificate reissue dates."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    }, follow_redirects=True)
    client.get('/auth/logout')

    with app.app_context():
        sample = Sample.query.first()
        assignment = SampleAssignment.query.first()
        sample.deputy_reviewed_at = datetime(2026, 1, 20, 10, 30)
        sample.certificate_prepared_at = datetime(2026, 1, 22, 11, 45)
        assignment.reviewed_at = datetime(2026, 1, 18, 9, 15)
        db.session.commit()
        sample_id = sample.id
        assignment_id = assignment.id

    _login(client, 'officer')

    resp = client.post(f'/samples/{sample_id}/request-backdate', data={
        'field_name': 'reviewed_at',
        'assignment_id': str(assignment_id),
        'proposed_date': '2026-01-17',
        'reason': 'Senior review completed earlier.',
    }, follow_redirects=True)
    assert b'submitted for approval' in resp.data

    resp = client.post(f'/samples/{sample_id}/request-backdate', data={
        'field_name': 'deputy_reviewed_at',
        'assignment_id': '0',
        'proposed_date': '2026-01-19',
        'reason': 'Deputy review date correction.',
    }, follow_redirects=True)
    assert b'submitted for approval' in resp.data

    resp = client.post(f'/samples/{sample_id}/request-backdate', data={
        'field_name': 'certificate_prepared_at',
        'assignment_id': '0',
        'proposed_date': '2026-01-21',
        'reason': 'Certificate reissue date correction.',
    }, follow_redirects=True)
    assert b'submitted for approval' in resp.data

    with app.app_context():
        from app.models import BackDateRequest
        assert BackDateRequest.query.filter_by(
            sample_id=sample_id, field_name='reviewed_at'
        ).count() == 1
        assert BackDateRequest.query.filter_by(
            sample_id=sample_id, field_name='deputy_reviewed_at'
        ).count() == 1
        assert BackDateRequest.query.filter_by(
            sample_id=sample_id, field_name='certificate_prepared_at'
        ).count() == 1


def test_backdate_decision_applies_senior_and_deputy_review_dates(app, client):
    """Approved back-date requests should update senior review and deputy review dates."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    }, follow_redirects=True)
    client.get('/auth/logout')

    with app.app_context():
        sample = Sample.query.first()
        assignment = SampleAssignment.query.first()
        sample.deputy_reviewed_at = datetime(2026, 2, 15, 8, 0)
        assignment.reviewed_at = datetime(2026, 2, 14, 14, 30)
        db.session.commit()
        sample_id = sample.id
        assignment_id = assignment.id

    _login(client, 'officer')
    client.post(f'/samples/{sample_id}/request-backdate', data={
        'field_name': 'reviewed_at',
        'assignment_id': str(assignment_id),
        'proposed_date': '2026-02-10',
        'reason': 'Senior review date fix',
    }, follow_redirects=True)
    client.post(f'/samples/{sample_id}/request-backdate', data={
        'field_name': 'deputy_reviewed_at',
        'assignment_id': '0',
        'proposed_date': '2026-02-11',
        'reason': 'Deputy review date fix',
    }, follow_redirects=True)
    client.get('/auth/logout')

    _login(client, 'hod')
    with app.app_context():
        from app.models import BackDateRequest
        req_reviewed = BackDateRequest.query.filter_by(
            sample_id=sample_id, field_name='reviewed_at', status='pending'
        ).first()
        req_deputy = BackDateRequest.query.filter_by(
            sample_id=sample_id, field_name='deputy_reviewed_at', status='pending'
        ).first()

    client.post(f'/backdate-requests/{req_reviewed.id}/decide', data={
        'decision': 'approved',
        'comments': 'Approved',
    }, follow_redirects=True)
    client.post(f'/backdate-requests/{req_deputy.id}/decide', data={
        'decision': 'approved',
        'comments': 'Approved',
    }, follow_redirects=True)

    with app.app_context():
        sample = Sample.query.first()
        assignment = SampleAssignment.query.first()
        assert assignment.reviewed_at.date().isoformat() == '2026-02-10'
        assert sample.deputy_reviewed_at.date().isoformat() == '2026-02-11'


# ---------------------------------------------------------------------------
# Resubmit to Deputy
# ---------------------------------------------------------------------------

def test_resubmit_to_deputy_redirects(app, client):
    """Resubmitting to deputy should redirect properly, not download HTML."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)

    _login(client, 'officer')
    _register_sample(client)
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/assign', data={
        'chemist_ids': [chemist_id],
        'test_name': 'Analysis',
    })
    client.get('/auth/logout')

    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Results.',
        'report_file': _report_file(),
    }, content_type='multipart/form-data')
    client.get('/auth/logout')

    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'approved', 'review_comments': 'OK',
    })
    client.get('/auth/logout')

    _login(client, 'senior')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/review', data={
        'action': 'accepted', 'review_comments': 'Good.',
    })

    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/submit-to-deputy', data={
        'summary_report': '',
    })

    # Deputy returns
    client.get('/auth/logout')
    _login(client, 'deputy')
    with app.app_context():
        sample = Sample.query.first()
    client.post(f'/samples/{sample.id}/deputy-review', data={
        'action': 'returned',
        'review_comments': 'Needs corrections.',
    })
    client.get('/auth/logout')

    # Senior chemist resubmits to deputy
    _login(client, 'senior')
    with app.app_context():
        sample = Sample.query.first()
        assert sample.status == SampleStatus.DEPUTY_RETURNED

    resp = client.post(
        f'/samples/{sample.id}/resubmit-to-deputy',
        follow_redirects=False,
    )
    # Should redirect, not return HTML content
    assert resp.status_code == 302
    assert '/samples/' in resp.headers['Location']

    # Following the redirect should work
    resp2 = client.get(resp.headers['Location'])
    assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# Sample dashboard advanced filters
# ---------------------------------------------------------------------------

def _create_sample_direct(app, **kwargs):
    """Create a Sample record directly in the DB and return its id."""
    defaults = dict(
        lab_number='TST/001',
        sample_name='Test Sample',
        sample_type=Branch.PHARMACEUTICAL,
        date_received=date.today(),
        date_registered=datetime.utcnow(),
        status=SampleStatus.REGISTERED,
    )
    defaults.update(kwargs)
    with app.app_context():
        s = Sample(**defaults)
        db.session.add(s)
        db.session.commit()
        return s.id


def test_dashboard_filter_by_formulation_type(app, client):
    """Dashboard should filter samples by formulation_type."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='PH/001', sample_name='Drug A', sample_type=Branch.PHARMACEUTICAL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, formulation_type='Tablet',
                    uploaded_by=officer_id)
        s2 = Sample(lab_number='PH/002', sample_name='Drug B', sample_type=Branch.PHARMACEUTICAL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, formulation_type='Capsule',
                    uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?formulation_type=Tablet')
    assert resp.status_code == 200
    assert b'Drug A' in resp.data
    assert b'Drug B' not in resp.data


def test_dashboard_filter_by_api(app, client):
    """Dashboard should filter samples by API."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='PH/001', sample_name='Drug A', sample_type=Branch.PHARMACEUTICAL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, api='Amoxicillin',
                    uploaded_by=officer_id)
        s2 = Sample(lab_number='PH/002', sample_name='Drug B', sample_type=Branch.PHARMACEUTICAL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, api='Paracetamol',
                    uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?api=amoxicillin')
    assert resp.status_code == 200
    assert b'Drug A' in resp.data
    assert b'Drug B' not in resp.data


def test_dashboard_filter_by_source(app, client):
    """Dashboard should filter samples by source."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='PH/001', sample_name='Drug A', sample_type=Branch.PHARMACEUTICAL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, source='Manufacturer X',
                    uploaded_by=officer_id)
        s2 = Sample(lab_number='PH/002', sample_name='Drug B', sample_type=Branch.PHARMACEUTICAL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, source='Importer Y',
                    uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?source=manufacturer+x')
    assert resp.status_code == 200
    assert b'Drug A' in resp.data
    assert b'Drug B' not in resp.data


def test_dashboard_filter_by_parish(app, client):
    """Dashboard should filter milk samples by parish."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='ML/001', sample_name='Milk A', sample_type=Branch.FOOD_MILK,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, parish='Kingston',
                    uploaded_by=officer_id)
        s2 = Sample(lab_number='ML/002', sample_name='Milk B', sample_type=Branch.FOOD_MILK,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, parish='St. Andrew',
                    uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?parish=Kingston')
    assert resp.status_code == 200
    assert b'Milk A' in resp.data
    assert b'Milk B' not in resp.data


def test_dashboard_filter_by_milk_type(app, client):
    """Dashboard should filter milk samples by milk type (R/P)."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='ML/001', sample_name='RawMilkSampleAlpha', sample_type=Branch.FOOD_MILK,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, milk_type='R',
                    uploaded_by=officer_id)
        s2 = Sample(lab_number='ML/002', sample_name='ProcMilkSampleBeta', sample_type=Branch.FOOD_MILK,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, milk_type='P',
                    uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?milk_type=R')
    assert resp.status_code == 200
    assert b'RawMilkSampleAlpha' in resp.data
    assert b'ProcMilkSampleBeta' not in resp.data


def test_dashboard_filter_by_patient_name(app, client):
    """Dashboard should filter toxicology samples by patient name."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='TX/001', sample_name='Tox A', sample_type=Branch.TOXICOLOGY,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, patient_name='John Doe',
                    uploaded_by=officer_id)
        s2 = Sample(lab_number='TX/002', sample_name='Tox B', sample_type=Branch.TOXICOLOGY,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, patient_name='Jane Smith',
                    uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?patient_name=john+doe')
    assert resp.status_code == 200
    assert b'Tox A' in resp.data
    assert b'Tox B' not in resp.data


def test_dashboard_filter_by_tox_sample_type(app, client):
    """Dashboard should filter toxicology samples by toxicology_sample_type_name."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='TX/001', sample_name='Tox A', sample_type=Branch.TOXICOLOGY,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, toxicology_sample_type_name='Blood',
                    uploaded_by=officer_id)
        s2 = Sample(lab_number='TX/002', sample_name='Tox B', sample_type=Branch.TOXICOLOGY,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, toxicology_sample_type_name='Urine',
                    uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?tox_sample_type=blood')
    assert resp.status_code == 200
    assert b'Tox A' in resp.data
    assert b'Tox B' not in resp.data


def test_dashboard_filter_by_alcohol_type(app, client):
    """Dashboard should filter alcohol samples by alcohol_type."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='AL/001', sample_name='Alc A', sample_type=Branch.FOOD_ALCOHOL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, alcohol_type='Alcohol Determination',
                    uploaded_by=officer_id)
        s2 = Sample(lab_number='AL/002', sample_name='Alc B', sample_type=Branch.FOOD_ALCOHOL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED,
                    alcohol_type='Denatured Alcohol (bitrex)',
                    uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?alcohol_type=Alcohol+Determination')
    assert resp.status_code == 200
    assert b'Alc A' in resp.data
    assert b'Alc B' not in resp.data


def test_dashboard_filter_by_sample_name(app, client):
    """Dashboard should filter by dedicated sample_name advanced filter."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s1 = Sample(lab_number='PH/001', sample_name='Unique Alpha Drug', sample_type=Branch.PHARMACEUTICAL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, uploaded_by=officer_id)
        s2 = Sample(lab_number='PH/002', sample_name='Beta Drug', sample_type=Branch.PHARMACEUTICAL,
                    date_received=date.today(), date_registered=datetime.utcnow(),
                    status=SampleStatus.REGISTERED, uploaded_by=officer_id)
        db.session.add_all([s1, s2])
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/?sample_name=Alpha')
    assert resp.status_code == 200
    assert b'Unique Alpha Drug' in resp.data
    assert b'Beta Drug' not in resp.data


# A 1x1 PNG image for use in image-upload tests
_MINIMAL_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
    b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _png_file():
    return (io.BytesIO(_MINIMAL_PNG), 'sample.png')


def test_deputy_sets_accreditation_on_prepare_certificate(app, client):
    """Deputy can mark a sample accredited while preparing the certificate."""
    officer_id, _sc, _ch, _dep, _hod = _setup_users(app)
    sid = _create_sample_direct(
        app, uploaded_by=officer_id,
        status=SampleStatus.CERTIFICATE_PREPARATION,
    )

    _login(client, 'deputy')
    resp = client.post(f'/samples/{sid}/prepare-certificate', data={
        'certificate_text': 'Certified analysis.',
        'accreditation': 'accredited',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.is_accredited is True
        assert sample.status == SampleStatus.HOD_REVIEW


def test_accredited_icon_shown_on_sample_list(app, client):
    """An accredited sample displays the verification icon on the list."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s = Sample(lab_number='PH/ACC', sample_name='Accredited Drug',
                   sample_type=Branch.PHARMACEUTICAL, date_received=date.today(),
                   date_registered=datetime.utcnow(),
                   status=SampleStatus.CERTIFIED, is_accredited=True,
                   uploaded_by=officer_id)
        db.session.add(s)
        db.session.commit()

    _login(client, 'officer')
    resp = client.get('/samples/')
    assert resp.status_code == 200
    assert b'bi-patch-check-fill' in resp.data


def test_accredited_icon_shown_on_pharma_report(app, client):
    """Accredited pharmaceutical samples show the icon in the pharma report."""
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        s = Sample(lab_number='PH/RPT', sample_name='Accredited Drug',
                   sample_type=Branch.PHARMACEUTICAL, date_received=date.today(),
                   date_registered=datetime.utcnow(),
                   status=SampleStatus.IN_PROGRESS, is_accredited=True,
                   uploaded_by=officer_id)
        db.session.add(s)
        db.session.commit()

    _login(client, 'deputy')
    resp = client.get('/reports/pharma')
    assert resp.status_code == 200
    assert b'bi-patch-check-fill' in resp.data


def test_edit_accreditation_requires_permission(app, client):
    """A user without the Edit Accreditation permission cannot change status."""
    officer_id, *_ = _setup_users(app)
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/EDIT1',
        status=SampleStatus.CERTIFIED, is_accredited=True,
    )

    _login(client, 'officer')
    resp = client.post(f'/samples/{sid}/edit', data={
        'lab_number': 'PH/EDIT1',
        'sample_name': 'Test Sample',
        'sample_type': Branch.PHARMACEUTICAL.name,
        'accreditation': 'not_accredited',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        # Unchanged because the officer lacks the permission.
        assert sample.is_accredited is True


def test_edit_accreditation_with_permission(app, client):
    """A user granted the permission can edit an already-set accreditation."""
    from app.models import Permission, user_permissions
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        db.session.execute(user_permissions.insert().values(
            user_id=officer_id, permission=Permission.EDIT_ACCREDITATION
        ))
        db.session.commit()
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/EDIT2',
        status=SampleStatus.CERTIFIED, is_accredited=True,
    )

    _login(client, 'officer')
    resp = client.post(f'/samples/{sid}/edit', data={
        'lab_number': 'PH/EDIT2',
        'sample_name': 'Test Sample',
        'sample_type': Branch.PHARMACEUTICAL.name,
        'accreditation': 'not_accredited',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.is_accredited is False


def test_edit_accreditation_sets_status_when_unset(app, client):
    """A permitted user can set accreditation on a sample that had none."""
    from app.models import Permission, user_permissions
    officer_id, *_ = _setup_users(app)
    with app.app_context():
        db.session.execute(user_permissions.insert().values(
            user_id=officer_id, permission=Permission.EDIT_ACCREDITATION
        ))
        db.session.commit()
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/EDIT3',
        status=SampleStatus.CERTIFIED, is_accredited=None,
    )

    _login(client, 'officer')
    resp = client.post(f'/samples/{sid}/edit', data={
        'lab_number': 'PH/EDIT3',
        'sample_name': 'Test Sample',
        'sample_type': Branch.PHARMACEUTICAL.name,
        'accreditation': 'accredited',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.is_accredited is True


def test_upload_and_delete_sample_image(app, client):
    """A sample image can be uploaded, previewed, and removed."""
    from app.models import SampleImage
    officer_id, *_ = _setup_users(app)
    sid = _create_sample_direct(app, uploaded_by=officer_id)

    _login(client, 'officer')
    resp = client.post(
        f'/samples/{sid}/upload-image',
        data={'image': _png_file(), 'caption': 'Front view'},
        content_type='multipart/form-data', follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        images = SampleImage.query.filter_by(sample_id=sid).all()
        assert len(images) == 1
        assert images[0].caption == 'Front view'
        image_id = images[0].id

    # Image preview appears on detail page
    resp = client.get(f'/samples/{sid}')
    assert b'Sample Images' in resp.data

    # Delete the image
    resp = client.post(
        f'/samples/{sid}/images/{image_id}/delete', follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert SampleImage.query.filter_by(sample_id=sid).count() == 0


# ---------------------------------------------------------------------------
# Post-Certification Out-of-Spec Marking
# ---------------------------------------------------------------------------

def test_mark_out_of_spec_denied_without_permission(app, client):
    """A chemist without HOD/Deputy/Senior Chemist role or OOS_FLAG permission is denied."""
    officer_id, sc_id, chemist_id, deputy_id, hod_id = _setup_users(app)
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/OOS1',
        status=SampleStatus.CERTIFIED,
    )

    _login(client, 'chemist')
    resp = client.post(f'/samples/{sid}/mark-out-of-spec', data={
        'reason': 'Failed re-test.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.marked_oos_at is None


def test_mark_out_of_spec_by_hod(app, client):
    """HOD can mark a certified sample as out of spec."""
    officer_id, *_ = _setup_users(app)
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/OOS2',
        status=SampleStatus.CERTIFIED,
    )

    _login(client, 'hod')
    resp = client.post(f'/samples/{sid}/mark-out-of-spec', data={
        'reason': 'Confirmed failing result on retest.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.marked_oos_at is not None
        assert sample.oos_mark_reason == 'Confirmed failing result on retest.'
        # Status remains Certified – marking OOS does not revert the workflow.
        assert sample.status == SampleStatus.CERTIFIED


def test_mark_out_of_spec_by_deputy(app, client):
    """Deputy can mark a certified sample as out of spec (role-based)."""
    officer_id, *_ = _setup_users(app)
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/OOS3',
        status=SampleStatus.CERTIFIED,
    )

    _login(client, 'deputy')
    resp = client.post(f'/samples/{sid}/mark-out-of-spec', data={
        'reason': 'OOS confirmed by Deputy.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.marked_oos_at is not None


def test_mark_out_of_spec_by_senior_chemist(app, client):
    """Senior Chemist can mark a certified sample as out of spec (role-based)."""
    officer_id, *_ = _setup_users(app)
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/OOS4',
        status=SampleStatus.CERTIFIED,
    )

    _login(client, 'senior')
    resp = client.post(f'/samples/{sid}/mark-out-of-spec', data={
        'reason': 'OOS confirmed by Senior Chemist.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.marked_oos_at is not None


def test_mark_out_of_spec_with_explicit_permission_grant(app, client):
    """A user without a qualifying role can act if explicitly granted OOS_FLAG."""
    officer_id, *_ = _setup_users(app)
    from app.models import user_permissions
    with app.app_context():
        db.session.execute(
            user_permissions.insert().values(
                user_id=officer_id, permission=Permission.OOS_FLAG
            )
        )
        db.session.commit()

    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/OOS5',
        status=SampleStatus.CERTIFIED,
    )

    _login(client, 'officer')
    resp = client.post(f'/samples/{sid}/mark-out-of-spec', data={
        'reason': 'OOS confirmed via granted permission.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.marked_oos_at is not None


def test_mark_out_of_spec_requires_certified_status(app, client):
    """Only certified samples can be marked out of spec."""
    officer_id, *_ = _setup_users(app)
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/OOS6',
        status=SampleStatus.IN_PROGRESS,
    )

    _login(client, 'hod')
    resp = client.post(f'/samples/{sid}/mark-out-of-spec', data={
        'reason': 'Attempted early mark.',
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        sample = db.session.get(Sample, sid)
        assert sample.marked_oos_at is None


def test_mark_out_of_spec_shows_badge_on_detail_page(app, client):
    """Once marked, the detail page shows an Out of Spec badge."""
    officer_id, *_ = _setup_users(app)
    sid = _create_sample_direct(
        app, uploaded_by=officer_id, lab_number='PH/OOS7',
        status=SampleStatus.CERTIFIED,
    )

    _login(client, 'hod')
    client.post(f'/samples/{sid}/mark-out-of-spec', data={
        'reason': 'Confirmed OOS.',
    }, follow_redirects=True)

    resp = client.get(f'/samples/{sid}')
    assert resp.status_code == 200
    assert b'Out of Spec' in resp.data
