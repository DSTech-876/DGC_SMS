import time
from datetime import timedelta

from flask import render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import OperationalError

from app import db
from app.auth import auth_bp
from app.forms import LoginForm, UserCreateForm, UserEditForm, ForgotPasswordForm, ResetPasswordForm, ChangePasswordForm, ActingRoleForm
from app.models import User, Role, Branch, Permission, Notification, SampleHistory, SampleAssignment, Sample, CustomRole, Setting, ActingRole, jamaica_now, AuditLog, ROLE_INHERENT_PERMISSIONS
from app.notifications import send_email


def _commit_with_retry(max_attempts=3, base_delay=0.2):
    """Commit with a short retry loop for transient SQLite lock errors."""
    for attempt in range(1, max_attempts + 1):
        try:
            db.session.commit()
            return
        except OperationalError as exc:
            db.session.rollback()
            if 'database is locked' in str(exc).lower() and attempt < max_attempts:
                time.sleep(base_delay * attempt)
                continue
            raise


def _role_hidden_key(role):
    return f'role_hidden_{role.name}'


def _role_inactive_key(role):
    return f'role_inactive_{role.name}'


def _get_builtin_role_state(role):
    return {
        'hidden': Setting.get_bool(_role_hidden_key(role), default=False),
        'inactive': Setting.get_bool(_role_inactive_key(role), default=False),
    }


def _is_builtin_role_assignable(role):
    state = _get_builtin_role_state(role)
    return not state['hidden'] and not state['inactive']


def _set_role_choices(form, include_custom=False, include_system_roles=None):
    """Populate role choices with system roles plus admin-defined custom roles."""
    include_system_roles = include_system_roles or set()
    choices = []
    for r in Role:
        if r.name in include_system_roles or _is_builtin_role_assignable(r):
            choices.append((r.name, r.value))
    if include_custom:
        custom_roles = CustomRole.query.order_by(CustomRole.name.asc()).all()
        choices.extend((f'custom:{r.id}', f'{r.name} (Custom)') for r in custom_roles)
    form.roles.choices = choices


def _split_selected_roles(selected_values):
    """Parse submitted role tokens into system-role enums and custom-role rows."""
    system_roles = set()
    custom_ids = []
    for token in (selected_values or []):
        if token.startswith('custom:'):
            _, _, raw_id = token.partition(':')
            try:
                custom_ids.append(int(raw_id))
            except ValueError:
                continue
            continue
        try:
            system_roles.add(Role[token])
        except KeyError:
            continue

    custom_roles = []
    if custom_ids:
        custom_roles = CustomRole.query.filter(
            CustomRole.id.in_(set(custom_ids))
        ).all()
    return system_roles, custom_roles


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.is_locked:
            flash(
                'This account is temporarily locked due to too many failed '
                'login attempts. Please try again later.',
                'danger',
            )
            return render_template('auth/login.html', form=form)
        if user and user.check_password(form.password.data) and user.is_active_user:
            user.reset_failed_logins()
            db.session.add(AuditLog(
                action='USER_LOGIN',
                entity_type='User',
                entity_id=user.id,
                entity_label=user.username,
                details=f'User "{user.username}" logged in.',
                performed_by=user.id,
                performed_at=jamaica_now(),
            ))
            db.session.commit()
            login_user(user, remember=form.remember_me.data)
            # Store remember-me preference in session so templates can
            # skip the idle-timeout auto-logout for remembered users.
            session['remember_me'] = form.remember_me.data
            if user.must_change_password:
                flash('Please change your password before continuing.', 'warning')
                return redirect(url_for('auth.change_password'))
            next_page = request.args.get('next')
            # Only allow safe relative redirects to prevent open-redirect
            if next_page and (
                not next_page.startswith('/')
                or next_page.startswith('//')
            ):
                next_page = None
            return redirect(next_page or url_for('main.dashboard'))
        # Record the failed attempt
        if user:
            user.record_failed_login()
            db.session.commit()
        flash('Invalid username or password.', 'danger')
    return render_template('auth/login.html', form=form)


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.is_active_user:
            token = user.get_reset_token()
            reset_url = url_for('auth.reset_password', token=token, _external=True)
            send_email(
                subject='[DGC SMS] Password Reset Request',
                recipients=[user.email],
                body_text=(
                    f'Hello {user.first_name},\n\n'
                    f'To reset your password, visit the following link:\n{reset_url}\n\n'
                    f'This link will expire in 30 minutes.\n'
                    f'If you did not request a password reset, please ignore this email.'
                ),
            )
        # Always show the same message to prevent user enumeration
        flash('If an account with that email exists, a password reset link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html', form=form)


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    user = User.verify_reset_token(token)
    if not user:
        flash('The reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been reset. You can now log in.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', form=form)


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash('Current password is incorrect.', 'danger')
        else:
            current_user.set_password(form.password.data)
            current_user.must_change_password = False
            db.session.commit()
            flash('Your password has been changed.', 'success')
            return redirect(url_for('main.dashboard'))
    return render_template('auth/change_password.html', form=form)


@auth_bp.route('/logout')
@login_required
def logout():
    db.session.add(AuditLog(
        action='USER_LOGOUT',
        entity_type='User',
        entity_id=current_user.id,
        entity_label=current_user.username,
        details=f'User "{current_user.username}" logged out.',
        performed_by=current_user.id,
        performed_at=jamaica_now(),
    ))
    db.session.commit()
    logout_user()
    # Remove only application-specific session keys.  Do NOT call
    # session.clear() here — logout_user() sets session['_remember'] =
    # 'clear' so Flask-Login's after_request handler will delete the
    # remember-me cookie.  Clearing the entire session would remove that
    # flag and leave the cookie intact, preventing logout.
    session.pop('remember_me', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ---------------------------------------------------------------------------
# User management (Admin only)
# ---------------------------------------------------------------------------

@auth_bp.route('/users')
@login_required
def user_list():
    # Authorization flow: explicit MANAGE_USERS permission grants take
    # precedence — checked via has_permission() — before role-based access.
    if not (current_user.has_permission(Permission.MANAGE_USERS)
            or current_user.has_any_role(Role.ADMIN, Role.HOD)):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    users = User.query.order_by(User.last_name).all()
    return render_template('auth/user_list.html', users=users)


@auth_bp.route('/users/create', methods=['GET', 'POST'])
@login_required
def user_create():
    # Explicit MANAGE_USERS permission overrides role restrictions.
    if not (current_user.has_permission(Permission.MANAGE_USERS)
            or current_user.has_any_role(Role.ADMIN, Role.HOD)):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    form = UserCreateForm()
    _set_role_choices(form, include_custom=current_user.has_role(Role.ADMIN))
    if form.validate_on_submit():
        plain_password = form.password.data
        user = User(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            username=form.username.data,
            email=form.email.data,
        )
        user.set_password(plain_password)
        user.must_change_password = True
        roles_set, custom_roles = _split_selected_roles(form.roles.data)
        branches_set = {Branch[b] for b in (form.branches.data or [])}
        permissions_set = {Permission[p] for p in (form.permissions.data or [])}
        user.roles = roles_set
        user.custom_roles_rel = custom_roles
        user.branches = branches_set
        user.permissions = permissions_set
        # Populate legacy single-value columns (production DB may have NOT NULL)
        user.role = next(iter(roles_set), Role.VIEWER)
        user.branch = next(iter(branches_set), None)
        db.session.add(user)
        try:
            db.session.add(AuditLog(
                action='USER_CREATED',
                entity_type='User',
                entity_id=None,
                entity_label=user.username,
                details=f'User "{user.username}" created by "{current_user.username}".',
                performed_by=current_user.id,
                performed_at=jamaica_now(),
            ))
            _commit_with_retry()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Failed to create user %r', form.username.data)
            flash(f'An error occurred while creating the user: {exc}', 'danger')
            return render_template('auth/user_form.html', form=form, title='Create User')
        # Send welcome email with login credentials
        try:
            login_url = url_for('auth.login', _external=True)
            send_email(
                subject='[DGC SMS] Your Account Has Been Created',
                recipients=[user.email],
                body_text=(
                    f'Hello {user.first_name},\n\n'
                    f'An account has been created for you in the DGC Sample Management System.\n\n'
                    f'Your login details are:\n'
                    f'  Username: {user.username}\n'
                    f'  Password: {plain_password}\n\n'
                    f'Please log in at: {login_url}\n\n'
                    f'You will be required to change your password on first login.\n\n'
                    f'If you did not expect this email, please contact your system administrator.'
                ),
            )
        except Exception:
            current_app.logger.exception(
                'Failed to send welcome email to %r', user.email
            )
        flash(f'User {user.username} created successfully.', 'success')
        return redirect(url_for('auth.user_list'))
    return render_template('auth/user_form.html', form=form, title='Create User')


@auth_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def user_edit(user_id):
    # Explicit MANAGE_USERS permission overrides role restrictions.
    if not (current_user.has_permission(Permission.MANAGE_USERS)
            or current_user.has_any_role(Role.ADMIN, Role.HOD)):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    user = db.get_or_404(User, user_id)
    form = UserEditForm(obj=user)
    _set_role_choices(
        form,
        include_custom=current_user.has_role(Role.ADMIN),
        include_system_roles={r.name for r in user.roles},
    )
    if request.method == 'GET':
        form.roles.data = [r.name for r in user.roles]
        if current_user.has_role(Role.ADMIN):
            form.roles.data.extend(f'custom:{r.id}' for r in user.custom_roles_rel)
        form.branches.data = [b.name for b in user.branches]
        form.permissions.data = [p.name for p in user.permissions]
    if form.validate_on_submit():
        # Check email uniqueness
        existing = User.query.filter(
            User.email == form.email.data, User.id != user.id
        ).first()
        if existing:
            flash('Email already in use by another user.', 'danger')
        else:
            user.first_name = form.first_name.data
            user.last_name = form.last_name.data
            user.email = form.email.data
            roles_set, custom_roles = _split_selected_roles(form.roles.data)
            branches_set = {Branch[b] for b in form.branches.data}
            user.roles = roles_set
            if current_user.has_role(Role.ADMIN):
                user.custom_roles_rel = custom_roles
            user.branches = branches_set
            # Only admins can see and edit the extra-permissions section; skip
            # updating permissions for HOD users to avoid accidentally clearing
            # permissions that were previously granted by an admin.
            if current_user.has_role(Role.ADMIN):
                permissions_set = {Permission[p] for p in (form.permissions.data or [])}
                user.permissions = permissions_set
            # Keep legacy single-value columns in sync
            user.role = next(iter(roles_set), Role.VIEWER)
            user.branch = next(iter(branches_set), None)
            user.is_active_user = form.is_active_user.data
            if form.new_password.data:
                user.set_password(form.new_password.data)
            try:
                db.session.add(AuditLog(
                    action='USER_UPDATED',
                    entity_type='User',
                    entity_id=user.id,
                    entity_label=user.username,
                    details=f'User "{user.username}" updated by "{current_user.username}".',
                    performed_by=current_user.id,
                    performed_at=jamaica_now(),
                ))
                _commit_with_retry()
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception('Failed to update user %r', user.username)
                flash(f'An error occurred while updating the user: {exc}', 'danger')
                return render_template('auth/user_form.html', form=form, title='Edit User', user=user)
            flash(f'User {user.username} updated.', 'success')
            return redirect(url_for('auth.user_list'))
    return render_template('auth/user_form.html', form=form, title='Edit User', user=user)


@auth_bp.route('/users/<int:user_id>/unlock', methods=['POST'])
@login_required
def user_unlock(user_id):
    if not current_user.has_role(Role.ADMIN):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    user = db.get_or_404(User, user_id)
    if user.is_locked:
        user.reset_failed_logins()
        try:
            _commit_with_retry()
        except Exception:
            db.session.rollback()
            flash('An error occurred while unlocking the account. Please try again.', 'danger')
            return redirect(url_for('auth.user_list'))
        flash(f'Account for {user.username} has been unlocked.', 'success')
    else:
        flash(f'Account for {user.username} is not locked.', 'info')
    return redirect(url_for('auth.user_list'))


@auth_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
def user_delete(user_id):
    if not current_user.has_role(Role.ADMIN):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('auth.user_list'))

    # Block deletion if the user owns samples, is assigned as chemist,
    # performed actions in history, or is referenced as a reviewer/assigner.
    has_samples = user.uploaded_samples.count() > 0
    has_assignments = user.assignments.count() > 0
    has_history = SampleHistory.query.filter_by(performed_by=user.id).first() is not None
    is_assigner = SampleAssignment.query.filter_by(assigned_by=user.id).first() is not None
    is_reviewer = SampleAssignment.query.filter_by(reviewed_by=user.id).first() is not None
    is_prelim_reviewer = SampleAssignment.query.filter_by(preliminary_reviewed_by=user.id).first() is not None
    is_sample_ref = (
        Sample.query.filter(
            (Sample.summary_report_by == user.id)
            | (Sample.deputy_reviewed_by == user.id)
            | (Sample.certificate_prepared_by == user.id)
            | (Sample.hod_reviewed_by == user.id)
            | (Sample.certified_by == user.id)
        ).first()
        is not None
    )

    if any((has_samples, has_assignments, has_history, is_assigner,
            is_reviewer, is_prelim_reviewer, is_sample_ref)):
        flash(
            f'Cannot delete {user.username} — they have related records. '
            'Deactivate the account instead.',
            'warning',
        )
        return redirect(url_for('auth.user_list'))

    # Safe to delete — clean up notifications and role/branch/permission associations first
    Notification.query.filter_by(user_id=user.id).delete()
    db.session.execute(
        db.text('DELETE FROM user_roles WHERE user_id = :uid'), {'uid': user.id}
    )
    db.session.execute(
        db.text('DELETE FROM user_branches WHERE user_id = :uid'), {'uid': user.id}
    )
    db.session.execute(
        db.text('DELETE FROM user_permissions WHERE user_id = :uid'), {'uid': user.id}
    )
    db.session.execute(
        db.text('DELETE FROM user_custom_roles WHERE user_id = :uid'), {'uid': user.id}
    )
    db.session.add(AuditLog(
        action='USER_DELETED',
        entity_type='User',
        entity_id=user.id,
        entity_label=user.username,
        details=f'User "{user.username}" deleted by "{current_user.username}".',
        performed_by=current_user.id,
        performed_at=jamaica_now(),
    ))
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} has been deleted.', 'success')
    return redirect(url_for('auth.user_list'))


# ---------------------------------------------------------------------------
# Active / logged-in users (Admin only)
# ---------------------------------------------------------------------------

_ONLINE_THRESHOLD_MINUTES = 15


@auth_bp.route('/active-users')
@login_required
def active_users():
    if not current_user.has_role(Role.ADMIN):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    # Strip timezone for SQLite comparison (DB stores naive datetimes)
    cutoff = jamaica_now().replace(tzinfo=None) - timedelta(minutes=_ONLINE_THRESHOLD_MINUTES)
    online = (
        User.query
        .filter(User.last_seen >= cutoff, User.is_active_user.is_(True))
        .order_by(User.last_seen.desc())
        .all()
    )
    return render_template(
        'auth/active_users.html',
        online_users=online,
        threshold_minutes=_ONLINE_THRESHOLD_MINUTES,
    )


# ---------------------------------------------------------------------------
# Acting Roles Management (Admin / HOD only)
# ---------------------------------------------------------------------------

@auth_bp.route('/acting-roles')
@login_required
def acting_roles_list():
    if not current_user.has_any_role(Role.ADMIN, Role.HOD):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    acting_roles = ActingRole.query.order_by(ActingRole.expiry_date.desc()).all()
    return render_template('auth/acting_roles.html', acting_roles=acting_roles)


@auth_bp.route('/acting-roles/assign', methods=['GET', 'POST'])
@login_required
def acting_role_assign():
    if not current_user.has_any_role(Role.ADMIN, Role.HOD):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    form = ActingRoleForm()
    users = User.query.filter_by(is_active_user=True).order_by(User.last_name).all()
    form.user_id.choices = [(u.id, f'{u.full_name} ({u.username})') for u in users]
    if form.validate_on_submit():
        acting_role = ActingRole(
            user_id=form.user_id.data,
            role=Role[form.role.data],
            assigned_by=current_user.id,
            start_date=form.start_date.data,
            expiry_date=form.expiry_date.data,
            notes=form.notes.data or None,
        )
        db.session.add(acting_role)
        target_user = db.get_or_404(User, form.user_id.data)
        db.session.add(AuditLog(
            action='ACTING_ROLE_ASSIGNED',
            entity_type='User',
            entity_id=target_user.id,
            entity_label=target_user.username,
            details=(
                f'Acting role "{Role[form.role.data].value}" assigned to '
                f'"{target_user.username}" by "{current_user.username}" '
                f'(expires {form.expiry_date.data}).'
            ),
            performed_by=current_user.id,
            performed_at=jamaica_now(),
        ))
        try:
            _commit_with_retry()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Failed to assign acting role')
            flash(f'An error occurred: {exc}', 'danger')
            return render_template('auth/acting_role_form.html', form=form, title='Assign Acting Role')
        flash(
            f'Acting role "{Role[form.role.data].value}" assigned to {target_user.full_name}.',
            'success',
        )
        return redirect(url_for('auth.acting_roles_list'))
    return render_template('auth/acting_role_form.html', form=form, title='Assign Acting Role')


@auth_bp.route('/acting-roles/<int:acting_role_id>/revoke', methods=['POST'])
@login_required
def acting_role_revoke(acting_role_id):
    if not current_user.has_any_role(Role.ADMIN, Role.HOD):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    acting_role = db.get_or_404(ActingRole, acting_role_id)
    target_user = acting_role.user
    db.session.add(AuditLog(
        action='ACTING_ROLE_REVOKED',
        entity_type='User',
        entity_id=target_user.id,
        entity_label=target_user.username,
        details=(
            f'Acting role "{acting_role.role.value}" revoked from '
            f'"{target_user.username}" by "{current_user.username}".'
        ),
        performed_by=current_user.id,
        performed_at=jamaica_now(),
    ))
    db.session.delete(acting_role)
    try:
        _commit_with_retry()
    except Exception as exc:
        db.session.rollback()
        flash(f'An error occurred: {exc}', 'danger')
        return redirect(url_for('auth.acting_roles_list'))
    flash(f'Acting role revoked from {target_user.full_name}.', 'success')
    return redirect(url_for('auth.acting_roles_list'))


# ---------------------------------------------------------------------------
# Roles & Permissions matrix (Admin only)
# ---------------------------------------------------------------------------

# Mapping from Role → set of Permission values that the role inherently has
# (before any per-user extra grants are applied).  Used for the read-only
# reference matrix. Defined in app.models as the single source of truth so
# that both the authorization logic (User.has_permission) and this UI-only
# reference matrix stay in sync.
_ROLE_INHERENT_PERMISSIONS: dict[Role, set[Permission]] = ROLE_INHERENT_PERMISSIONS


@auth_bp.route('/roles-permissions', methods=['GET', 'POST'])
@login_required
def roles_permissions():
    if not current_user.has_role(Role.ADMIN):
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        action = request.form.get('action', '').strip()
        if action == 'update_builtin_role_state':
            role_name = (request.form.get('role_name') or '').strip()
            role = Role[role_name] if role_name in Role.__members__ else None
            if role is None:
                flash('Built-in role not found.', 'danger')
                return redirect(url_for('auth.roles_permissions'))
            if role == Role.ADMIN:
                flash('Admin role cannot be hidden or inactivated.', 'warning')
                return redirect(url_for('auth.roles_permissions'))

            hidden = request.form.get('hidden') == 'on'
            inactive = request.form.get('inactive') == 'on'
            Setting.set(_role_hidden_key(role), str(hidden).lower())
            Setting.set(_role_inactive_key(role), str(inactive).lower())
            _commit_with_retry()
            flash(f'Updated built-in role settings for {role.display_name}.', 'success')
            return redirect(url_for('auth.roles_permissions'))

        if action == 'bulk_migrate_builtin_role':
            source_name = (request.form.get('source_role') or '').strip()
            target_id = request.form.get('target_custom_role_id', type=int)

            if source_name not in Role.__members__:
                flash('Select a valid source built-in role.', 'danger')
                return redirect(url_for('auth.roles_permissions'))
            source_role = Role[source_name]
            if source_role == Role.ADMIN:
                flash('Bulk migration from Admin is not allowed.', 'danger')
                return redirect(url_for('auth.roles_permissions'))

            target_role = db.session.get(CustomRole, target_id) if target_id else None
            if target_role is None:
                flash('Select a valid target custom role.', 'danger')
                return redirect(url_for('auth.roles_permissions'))

            users = User.query.all()
            migrated = 0
            for user in users:
                if source_role not in user.roles:
                    continue
                roles = set(user.roles)
                roles.discard(source_role)
                if not roles:
                    roles.add(Role.VIEWER)
                user.roles = roles

                custom_roles = list(user.custom_roles_rel)
                if all(r.id != target_role.id for r in custom_roles):
                    custom_roles.append(target_role)
                user.custom_roles_rel = custom_roles

                user.role = next(iter(roles), Role.VIEWER)
                migrated += 1

            _commit_with_retry()
            flash(
                f'Migrated {migrated} user(s) from {source_role.display_name} '
                f'to custom role "{target_role.name}".',
                'success',
            )
            return redirect(url_for('auth.roles_permissions'))

        if action == 'create_custom_role':
            role_name = (request.form.get('role_name') or '').strip()
            role_description = (request.form.get('role_description') or '').strip()
            permission_values = request.form.getlist('permissions')

            if not role_name:
                flash('Role name is required.', 'danger')
                return redirect(url_for('auth.roles_permissions'))

            normalized = role_name.lower()
            existing_system = {
                r.name.lower() for r in Role
            } | {
                r.value.lower() for r in Role
            } | {
                r.display_name.lower() for r in Role
            }
            if normalized in existing_system:
                flash('Role name conflicts with an existing system role.', 'danger')
                return redirect(url_for('auth.roles_permissions'))

            existing_custom = CustomRole.query.filter(
                db.func.lower(CustomRole.name) == normalized
            ).first()
            if existing_custom:
                flash('A custom role with that name already exists.', 'danger')
                return redirect(url_for('auth.roles_permissions'))

            role = CustomRole(name=role_name, description=role_description or None)
            db.session.add(role)
            db.session.flush()
            parsed_permissions = {
                Permission[p] for p in permission_values if p in Permission.__members__
            }
            role.permissions = parsed_permissions
            _commit_with_retry()
            flash(f'Custom role "{role.name}" created.', 'success')
            return redirect(url_for('auth.roles_permissions'))

        if action == 'delete_custom_role':
            role_id = request.form.get('role_id', type=int)
            role = db.session.get(CustomRole, role_id) if role_id else None
            if role is None:
                flash('Custom role not found.', 'danger')
                return redirect(url_for('auth.roles_permissions'))
            if role.users:
                flash(
                    f'Cannot delete custom role "{role.name}" while assigned to users.',
                    'warning',
                )
                return redirect(url_for('auth.roles_permissions'))

            db.session.delete(role)
            _commit_with_retry()
            flash(f'Custom role "{role.name}" deleted.', 'success')
            return redirect(url_for('auth.roles_permissions'))

    all_users = User.query.filter_by(is_active_user=True).all()

    # Count of active users per role
    role_user_counts: dict[Role, int] = {
        role: sum(1 for u in all_users if role in u.roles)
        for role in Role
    }

    # Count of active users who have been explicitly granted each permission
    permission_user_counts: dict[Permission, int] = {
        perm: sum(1 for u in all_users if perm in u.permissions)
        for perm in Permission
    }

    # Alphabetically sorted roles by display_name for column order
    sorted_roles = sorted(Role, key=lambda r: r.display_name)
    custom_roles = CustomRole.query.order_by(CustomRole.name.asc()).all()
    built_in_role_states = {
        role.name: _get_builtin_role_state(role)
        for role in sorted_roles
    }
    custom_role_user_counts = {
        role.id: sum(1 for u in all_users if role in u.custom_roles_rel)
        for role in custom_roles
    }

    return render_template(
        'auth/roles_permissions.html',
        roles=sorted_roles,
        custom_roles=custom_roles,
        built_in_role_states=built_in_role_states,
        permissions=list(Permission),
        role_user_counts=role_user_counts,
        custom_role_user_counts=custom_role_user_counts,
        permission_user_counts=permission_user_counts,
        inherent=_ROLE_INHERENT_PERMISSIONS,
    )
