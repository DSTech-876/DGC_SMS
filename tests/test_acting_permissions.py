"""Regression tests for the effective-permission UNION model and for
acting roles being strictly additive (they must never reduce access)."""
import io
from datetime import timedelta

import pytest

from app import db
from app.models import (
    User, Role, Branch, Permission, ActingRole, Sample, SampleAssignment,
    ROLE_INHERENT_PERMISSIONS, jamaica_now, user_permissions,
)
from tests.conftest import _create_user, _login


def _grant(user, *permissions):
    for p in permissions:
        db.session.execute(
            user_permissions.insert().values(user_id=user.id, permission=p)
        )
    db.session.commit()


def _assign_acting(user, role, assigned_by, activated=True):
    """Assign an acting role. Acting roles are opt-in, so unless the test
    wants the dormant state, it is activated here as the user would do via
    the "Act as …" switch."""
    today = jamaica_now().date()
    acting = ActingRole(
        user_id=user.id,
        role=role,
        assigned_by=assigned_by.id,
        start_date=today,
        expiry_date=today + timedelta(days=30),
        is_activated=activated,
    )
    db.session.add(acting)
    db.session.commit()
    return acting


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


@pytest.mark.parametrize('primary_role', [r for r in Role if r != Role.ADMIN])
def test_direct_permission_survives_any_acting_role_for_every_primary_role(
    app, primary_role
):
    """For every non-admin primary role, a direct permission grant that is
    NOT part of that role's inherent set must remain in effective_permissions
    after ANY acting role is assigned, and must remain honored (not just
    "present" but actually usable) after re-fetching the user from a fresh
    session — simulating a brand-new request/login.

    This is the broad regression test requested for the "Additional User
    Permissions disappear once an Acting Role is assigned" report: it proves
    EffectivePermissions = PrimaryRolePermissions UNION DirectUserPermissions
    UNION ActingRolePermissions for the full role matrix, not just one case.
    """
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin_matrix')
        inherent = ROLE_INHERENT_PERMISSIONS.get(primary_role, set())
        direct_candidates = [p for p in Permission if p not in inherent]
        if not direct_candidates:
            pytest.skip('role already has every permission inherently')
        direct_perm = direct_candidates[0]

        user = _create_user(primary_role, username=f'matrix_{primary_role.name}')
        _grant(user, direct_perm)
        assert direct_perm in db.session.get(User, user.id).effective_permissions

        acting_role = next(r for r in Role if r != primary_role)
        _assign_acting(user, acting_role, admin)

        # Re-fetch as a brand-new ORM instance (simulates a new request).
        refreshed = db.session.get(User, user.id)
        assert direct_perm in refreshed.effective_permissions, (
            f'Direct permission {direct_perm} was suppressed after assigning '
            f'acting role {acting_role} to a {primary_role} user'
        )
        assert refreshed.has_permission(direct_perm)


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


# ---------------------------------------------------------------------------
# Acting roles are opt-in: assignment alone changes nothing
# ---------------------------------------------------------------------------

def test_assigned_acting_role_is_dormant_until_activated(app):
    """Assigning an acting role must not change access until the user
    switches into it."""
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin_opt')
        chemist = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chem_opt')
        acting = _assign_acting(chemist, Role.SENIOR_CHEMIST, admin, activated=False)

        assert acting.is_available
        assert not acting.is_active
        assert chemist.active_acting_roles == []
        assert [a.id for a in chemist.available_acting_roles] == [acting.id]
        assert not chemist.has_role(Role.SENIOR_CHEMIST)
        assert not chemist.has_permission(Permission.TECHNICAL_REVIEW)
        # Primary-role permissions are untouched
        assert chemist.effective_permissions == set(
            ROLE_INHERENT_PERMISSIONS.get(Role.CHEMIST, set())
        )


def test_activate_route_enables_acting_role_and_revert_restores_default(app, client):
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin_sw')
        chemist = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chem_sw')
        acting = _assign_acting(chemist, Role.SENIOR_CHEMIST, admin, activated=False)
        acting_id = acting.id

    _login(client, 'chem_sw')
    resp = client.post(f'/auth/acting-roles/{acting_id}/activate', follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(username='chem_sw').first()
        assert user.has_role(Role.SENIOR_CHEMIST)
        assert user.has_permission(Permission.TECHNICAL_REVIEW)
        # Primary role permissions are still present (strictly additive)
        assert ROLE_INHERENT_PERMISSIONS.get(Role.CHEMIST, set()) <= user.effective_permissions

    resp = client.post('/auth/acting-roles/revert', follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(username='chem_sw').first()
        assert not user.has_role(Role.SENIOR_CHEMIST)
        assert user.active_acting_roles == []
        # The assignment itself survives so the user can switch back
        assert len(user.available_acting_roles) == 1


def test_user_cannot_activate_another_users_acting_role(app, client):
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin_x')
        other = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chem_x')
        intruder = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chem_y')
        acting = _assign_acting(other, Role.SENIOR_CHEMIST, admin, activated=False)
        acting_id = acting.id

    _login(client, 'chem_y')
    client.post(f'/auth/acting-roles/{acting_id}/activate', follow_redirects=True)
    with app.app_context():
        victim = User.query.filter_by(username='chem_x').first()
        intruder = User.query.filter_by(username='chem_y').first()
        assert victim.active_acting_roles == []
        assert intruder.active_acting_roles == []


def test_activating_one_acting_role_deactivates_the_other(app, client):
    with app.app_context():
        admin = _create_user(Role.ADMIN, username='admin_two')
        chemist = _create_user(Role.CHEMIST, Branch.TOXICOLOGY, username='chem_two')
        first = _assign_acting(chemist, Role.SENIOR_CHEMIST, admin, activated=True)
        second = _assign_acting(chemist, Role.DEPUTY, admin, activated=False)
        second_id = second.id

    _login(client, 'chem_two')
    client.post(f'/auth/acting-roles/{second_id}/activate', follow_redirects=True)
    with app.app_context():
        user = User.query.filter_by(username='chem_two').first()
        active = user.active_acting_roles
        assert len(active) == 1
        assert active[0].role == Role.DEPUTY
