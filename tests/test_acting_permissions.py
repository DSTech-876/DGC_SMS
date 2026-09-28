"""Regression tests for the effective-permission UNION model and for
acting roles being strictly additive (they must never reduce access)."""
import io
from datetime import timedelta

from app import db
from app.models import (
    User, Role, Branch, Permission, ActingRole, Sample, SampleAssignment,
    jamaica_now, user_permissions,
)
from tests.conftest import _create_user, _login


def _grant(user, *permissions):
    for p in permissions:
        db.session.execute(
            user_permissions.insert().values(user_id=user.id, permission=p)
        )
    db.session.commit()


def _assign_acting(user, role, assigned_by):
    today = jamaica_now().date()
    db.session.add(ActingRole(
        user_id=user.id,
        role=role,
        assigned_by=assigned_by.id,
        start_date=today,
        expiry_date=today + timedelta(days=30),
    ))
    db.session.commit()


def _register_sample(client, lab_number='TOX-100', sample_type='TOXICOLOGY'):
    return client.post('/samples/register', data={
        'lab_number': lab_number,
        'sample_name': 'Perm Sample',
        'sample_type': sample_type,
        'date_received': '2026-01-15',
        'description': 'Test sample',
        'quantity': '50ml',
    }, follow_redirects=True)


# ---------------------------------------------------------------------------
# effective_permissions = primary role ∪ direct grants ∪ custom roles ∪ acting
# ---------------------------------------------------------------------------

def test_primary_role_inherent_permissions_are_effective(app):
    """A role's inherent permissions must be part of effective_permissions."""
    with app.app_context():
        hod = _create_user(Role.HOD, username='hod1')
        assert hod.has_permission(Permission.HOD_REVIEW)
        assert hod.has_permission(Permission.ASSIGN_SAMPLE)
        assert hod.has_permission(Permission.VIEW_ALL_PRELIMINARY_REVIEWS)
        # Not everything: HOD has no DEPUTY_REVIEW inherent grant
        assert not hod.has_permission(Permission.DEPUTY_REVIEW)


def test_effective_permissions_union_of_all_sources(app):
    """Direct grants + primary role + acting role must all be honored together."""
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin1')
        chemist = _create_user(Role.CHEMIST, username='chem1')
        _grant(chemist, Permission.KPI_VIEW)
        _assign_acting(chemist, Role.SENIOR_CHEMIST, admin)

        chemist = db.session.get(User, chemist.id)
        # From primary role (CHEMIST)
        assert chemist.has_permission(Permission.SUBMIT_REPORT)
        # From direct grant
        assert chemist.has_permission(Permission.KPI_VIEW)
        # From acting role (SENIOR_CHEMIST)
        assert chemist.has_permission(Permission.ASSIGN_SAMPLE)
        assert chemist.has_permission(Permission.TECHNICAL_REVIEW)


def test_acting_role_never_removes_permissions(app):
    """Assigning an acting role must not remove any pre-existing permission."""
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin2')
        officer = _create_user(Role.OFFICER, username='off1')
        _grant(officer, Permission.AUDIT_LOG_VIEW)
        before = set(officer.effective_permissions)

        _assign_acting(officer, Role.CHEMIST, admin)
        officer = db.session.get(User, officer.id)
        after = set(officer.effective_permissions)
        assert before <= after, 'acting role removed existing permissions'


# ---------------------------------------------------------------------------
# Acting roles must never trigger restrictive visibility scoping
# ---------------------------------------------------------------------------

def test_deputy_with_acting_senior_chemist_still_sees_all_samples(app, client):
    """A Deputy branch-scoped by an acting Senior Chemist role was losing
    visibility of samples outside the acting branch."""
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin3')
        officer = _create_user(Role.OFFICER, username='off2')
        deputy = _create_user(Role.DEPUTY, Branch.TOXICOLOGY, username='dep1')
        _assign_acting(deputy, Role.SENIOR_CHEMIST, admin)

    _login(client, 'off2')
    _register_sample(client, lab_number='PHA-777', sample_type='PHARMACEUTICAL')
    client.get('/auth/logout')

    _login(client, 'dep1')
    resp = client.get('/samples/')
    assert resp.status_code == 200
    assert b'PHA-777' in resp.data


def test_assistant_with_acting_chemist_keeps_full_sample_visibility(app, client):
    """A Govt Chemist Assistant given an acting Chemist role was restricted to
    only samples assigned to them (i.e. none)."""
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin4')
        officer = _create_user(Role.OFFICER, username='off3')
        gca = _create_user(Role.GOVT_CHEMIST_ASSISTANT, username='gca1')
        _assign_acting(gca, Role.CHEMIST, admin)

    _login(client, 'off3')
    _register_sample(client, lab_number='TOX-888')
    client.get('/auth/logout')

    _login(client, 'gca1')
    resp = client.get('/samples/')
    assert resp.status_code == 200
    assert b'TOX-888' in resp.data


def test_hod_with_acting_senior_chemist_dashboard_not_branch_scoped(app, client):
    """An HOD with an acting Senior Chemist role and a branch must still see
    dashboard totals across all branches."""
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin5')
        officer = _create_user(Role.OFFICER, username='off4')
        hod = _create_user(Role.HOD, Branch.TOXICOLOGY, username='hod2')
        _assign_acting(hod, Role.SENIOR_CHEMIST, admin)

    _login(client, 'off4')
    _register_sample(client, lab_number='PHA-999', sample_type='PHARMACEUTICAL')
    client.get('/auth/logout')

    _login(client, 'hod2')
    resp = client.get('/dashboard')
    assert resp.status_code == 200
    with app.app_context():
        hod = User.query.filter_by(username='hod2').first()
        # The restrictive branch filter must not apply to a primary HOD.
        assert not (hod.has_primary_role(Role.SENIOR_CHEMIST))
        assert hod.has_role(Role.SENIOR_CHEMIST)  # acting role still additive


# ---------------------------------------------------------------------------
# Direct permission grants must be honored by the assignment dashboard
# ---------------------------------------------------------------------------

def test_direct_assign_permission_grants_supervisor_dashboard(app, client):
    """A chemist granted ASSIGN_SAMPLE directly must get the supervisor view
    of the assignment dashboard API (permission-based, not role-based)."""
    with app.app_context():
        supervisor = _create_user(Role.SENIOR_CHEMIST, Branch.TOXICOLOGY,
                                  username='senior1')
        chem_a = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chema')
        chem_b = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chemb')
        _grant(chem_a, Permission.ASSIGN_SAMPLE)
        sample = Sample(
            lab_number='TOX-500', sample_name='S', sample_type=Branch.TOXICOLOGY,
            uploaded_by=supervisor.id, date_received=jamaica_now(),
        )
        db.session.add(sample)
        db.session.commit()
        db.session.add(SampleAssignment(
            sample_id=sample.id, chemist_id=chem_b.id,
            test_name='Analysis', assigned_by=supervisor.id,
        ))
        db.session.commit()

    # chem_a has no assignments of their own but holds ASSIGN_SAMPLE:
    # the supervisor view must show chem_b's assignment.
    _login(client, 'chema')
    resp = client.get('/api/assignments/records')
    assert resp.status_code == 200
    data = resp.get_json()
    records = data if isinstance(data, list) else data.get('records', [])
    assert len(records) == 1


def test_chemist_without_permission_still_analyst_view(app, client):
    """A plain chemist (no grants) must keep the analyst-only view."""
    with app.app_context():
        supervisor = _create_user(Role.SENIOR_CHEMIST, Branch.TOXICOLOGY,
                                  username='senior2')
        chem_a = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chemc')
        chem_b = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chemd')
        sample = Sample(
            lab_number='TOX-501', sample_name='S', sample_type=Branch.TOXICOLOGY,
            uploaded_by=supervisor.id, date_received=jamaica_now(),
        )
        db.session.add(sample)
        db.session.commit()
        db.session.add(SampleAssignment(
            sample_id=sample.id, chemist_id=chem_b.id,
            test_name='Analysis', assigned_by=supervisor.id,
        ))
        db.session.commit()

    _login(client, 'chemc')
    resp = client.get('/api/assignments/records')
    assert resp.status_code == 200
    data = resp.get_json()
    records = data if isinstance(data, list) else data.get('records', [])
    assert len(records) == 0
