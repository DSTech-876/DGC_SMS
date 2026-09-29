"""Email and in-app notification helpers."""

from datetime import date, timedelta
from threading import Thread

from flask import current_app, render_template_string
from flask_mail import Message

from app import db, mail
from app.models import Notification, User, Branch, Role, user_roles, user_branches


def _get_smtp_settings():
    """Read SMTP settings from the database, falling back to app config values.

    Returns a dict with keys: server, port, use_tls, username, password, sender.
    Returns None when no custom DB settings are configured (caller should use
    the default Flask-Mail setup).
    """
    from app.models import Setting
    server = Setting.get('smtp_server', '').strip()
    if not server:
        return None
    try:
        port = int(Setting.get('smtp_port', '587').strip() or 587)
    except (ValueError, TypeError):
        port = 587
    use_tls = Setting.get_bool('smtp_use_tls', default=True)
    username = Setting.get('smtp_username', '').strip() or None
    password = Setting.get('smtp_password', '').strip() or None
    sender = Setting.get('smtp_sender', '').strip() or None
    return {
        'server': server,
        'port': port,
        'use_tls': use_tls,
        'username': username,
        'password': password,
        'sender': sender,
    }


def _send_async_email(app, msg, smtp_cfg=None):
    with app.app_context():
        try:
            if smtp_cfg:
                import smtplib
                import ssl as _ssl
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText as _MIMEText

                sender = smtp_cfg.get('sender') or smtp_cfg.get('username') or ''
                if not sender:
                    app.logger.error(
                        'SMTP send skipped: no sender address configured. '
                        'Set a Sender Address or Username in the Mail Server settings.'
                    )
                    return

                mime = MIMEMultipart('alternative')
                mime['Subject'] = msg.subject
                mime['From'] = sender
                mime['To'] = ', '.join(msg.recipients)
                if msg.body:
                    mime.attach(_MIMEText(msg.body, 'plain'))
                if getattr(msg, 'html', None):
                    mime.attach(_MIMEText(msg.html, 'html'))

                context = _ssl.create_default_context()
                server = smtp_cfg['server']
                port = smtp_cfg['port']
                username = smtp_cfg.get('username')
                password = smtp_cfg.get('password')

                # Warn when credentials would be sent over an unencrypted connection
                if username and password and not smtp_cfg.get('use_tls'):
                    app.logger.warning(
                        'SMTP credentials are being sent without TLS encryption (port %s). '
                        'Enable TLS in the Mail Server settings if your server supports it.',
                        port,
                    )

                with smtplib.SMTP(server, port) as smtp:
                    if smtp_cfg.get('use_tls'):
                        smtp.starttls(context=context)
                    if username and password:
                        smtp.login(username, password)
                    smtp.sendmail(sender, msg.recipients, mime.as_string())
            else:
                mail.send(msg)
        except Exception as e:
            app.logger.error(f'Failed to send email: {e}')


def send_email(subject, recipients, body_text, body_html=None):
    """Send an email (non-blocking). Respects the email_enabled setting.

    SMTP connection settings are read from the database at send time so that
    admin changes take effect immediately without an application restart.
    """
    from app.models import Setting
    if not Setting.get_bool('email_enabled', default=True):
        current_app.logger.info(f'Email suppressed (disabled in settings): {subject}')
        return
    app = current_app._get_current_object()
    smtp_cfg = _get_smtp_settings()
    # Determine sender for the Message object
    sender = None
    if smtp_cfg:
        sender = smtp_cfg.get('sender') or smtp_cfg.get('username')
    msg = Message(subject=subject, recipients=recipients, body=body_text, sender=sender)
    if body_html:
        msg.html = body_html
    thread = Thread(target=_send_async_email, args=(app, msg, smtp_cfg))
    thread.daemon = True
    thread.start()


def _build_html_email(title, message, link=None):
    """Build a styled HTML email body with optional action link."""
    base_url = ''
    try:
        from flask import request
        base_url = request.host_url.rstrip('/')
    except RuntimeError:
        pass

    link_html = ''
    if link:
        full_url = f'{base_url}{link}' if base_url else link
        link_html = (
            f'<tr><td style="padding:20px 30px 0 30px;">'
            f'<a href="{full_url}" style="display:inline-block;padding:10px 24px;'
            f'background-color:#0d6efd;color:#ffffff;text-decoration:none;'
            f'border-radius:5px;font-weight:600;">View Details</a></td></tr>'
        )

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:0;background-color:#f4f6f9;'
        'font-family:Arial,Helvetica,sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="background-color:#f4f6f9;padding:30px 0;">'
        '<tr><td align="center">'
        '<table width="600" cellpadding="0" cellspacing="0" '
        'style="background-color:#ffffff;border-radius:8px;'
        'box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden;">'
        '<tr><td style="background-color:#0d6efd;padding:20px 30px;">'
        '<h1 style="margin:0;color:#ffffff;font-size:20px;">DGC SMS</h1>'
        '</td></tr>'
        f'<tr><td style="padding:24px 30px 8px 30px;">'
        f'<h2 style="margin:0 0 12px 0;color:#212529;font-size:18px;">'
        f'{title}</h2>'
        f'<p style="margin:0;color:#495057;font-size:14px;line-height:1.6;'
        f'white-space:pre-wrap;">{message}</p></td></tr>'
        f'{link_html}'
        '<tr><td style="padding:24px 30px;border-top:1px solid #e9ecef;'
        'margin-top:20px;">'
        '<p style="margin:0;color:#6c757d;font-size:12px;">'
        'This is an automated notification from the Department of Government '
        'Chemist&rsquo;s Sample Management System.<br>'
        'Please do not reply to this email.</p>'
        '</td></tr></table></td></tr></table></body></html>'
    )


def create_notification(user_id, title, message, link=None, send_mail=True):
    """Create an in-app notification and optionally send email."""
    notif = Notification(
        user_id=user_id,
        title=title,
        message=message,
        link=link,
    )
    db.session.add(notif)

    if send_mail:
        user = db.session.get(User, user_id)
        if user and user.email:
            html_body = _build_html_email(title, message, link)
            send_email(
                subject=f'[DGC SMS] {title}',
                recipients=[user.email],
                body_text=message,
                body_html=html_body,
            )
            notif.email_sent = True

    return notif


def notify_branch_heads(branch, title, message, link=None, exclude_user_id=None):
    """Notify Senior Chemist, Deputy and HOD for a given branch."""
    from app.models import Branch
    head_roles = [Role.SENIOR_CHEMIST, Role.DEPUTY, Role.HOD]
    heads = User.query.join(user_roles).filter(
        user_roles.c.role.in_(head_roles),
        User.is_active_user.is_(True),
    ).distinct().all()

    # Treat PHARMACEUTICAL_NR as PHARMACEUTICAL for branch-head matching
    effective_branch = branch
    if branch == Branch.PHARMACEUTICAL_NR:
        effective_branch = Branch.PHARMACEUTICAL

    # Filter by branch – HOD/Deputy may not have a branch (org-wide)
    for head in heads:
        if head.id == exclude_user_id:
            continue
        if head.branches and effective_branch not in head.branches and branch not in head.branches:
            continue
        create_notification(head.id, title, message, link)


def _notify_branch_officers(branch, title, message, link=None, exclude_user_id=None):
    """Notify active Officers assigned to the same branch.

    This ensures all officers in the relevant branch are aware of pending
    preliminary reviews, not just the one who originally uploaded the sample.
    """
    from app.models import Branch
    officers = User.query.join(user_roles).filter(
        user_roles.c.role == Role.OFFICER,
        User.is_active_user.is_(True),
    ).distinct().all()

    # Treat PHARMACEUTICAL_NR as PHARMACEUTICAL for branch matching
    effective_branch = branch
    if branch == Branch.PHARMACEUTICAL_NR:
        effective_branch = Branch.PHARMACEUTICAL

    for officer in officers:
        if officer.id == exclude_user_id:
            continue
        # Officers without branches are org-wide; otherwise filter by branch
        if officer.branches and effective_branch not in officer.branches and branch not in officer.branches:
            continue
        create_notification(officer.id, title, message, link)


def notify_sample_uploaded(sample):
    """Called when an officer uploads a new sample."""
    title = f'New Sample Registered: {sample.lab_number}'
    message = (
        f'Sample "{sample.sample_name}" (Lab# {sample.lab_number}) '
        f'of type {sample.sample_type.value} has been registered and '
        f'is awaiting assignment.'
    )
    link = f'/samples/{sample.id}'
    notify_branch_heads(sample.sample_type, title, message, link)


def notify_sample_assigned(assignment):
    """Called when a senior chemist assigns a sample to a chemist."""
    title = f'Sample Assigned: {assignment.sample.lab_number}'
    message = (
        f'You have been assigned test "{assignment.test_name}" for '
        f'sample "{assignment.sample.sample_name}" '
        f'(Lab# {assignment.sample.lab_number}).'
    )
    link = f'/samples/assignment/{assignment.id}'
    create_notification(assignment.chemist_id, title, message, link)


def notify_report_submitted(assignment):
    """Called when a chemist submits a report."""
    from app.models import AssignmentStatus, Branch

    sample = assignment.sample
    link = f'/samples/assignment/{assignment.id}/preliminary-review'

    # If returning directly to Senior Chemist review (bypassing preliminary),
    # only notify branch heads – officers don't need to act.
    if assignment.status == AssignmentStatus.UNDER_TECHNICAL_REVIEW:
        notify_branch_heads(
            sample.sample_type,
            f'Report Ready for Senior Chemist Review: {sample.lab_number}',
            f'Analyst {assignment.chemist.full_name} has resubmitted '
            f'report for test "{assignment.test_name}" on sample '
            f'"{sample.sample_name}" (Lab# {sample.lab_number}). '
            f'Senior Chemist review required.',
            f'/samples/assignment/{assignment.id}',
        )
        return

    # Notify the officer who uploaded the sample (for preliminary review)
    title = f'Preliminary Review Required: {sample.lab_number}'
    message = (
        f'Analyst {assignment.chemist.full_name} has submitted a report '
        f'for test "{assignment.test_name}" on sample '
        f'"{sample.sample_name}" (Lab# {sample.lab_number}). '
        f'A preliminary review is required.'
    )
    create_notification(sample.uploaded_by, title, message, link)

    # Also notify other officers in the same branch so coverage is not missed
    _notify_branch_officers(
        sample.sample_type, title, message, link,
        exclude_user_id=sample.uploaded_by,
    )

    # For pharmaceutical samples, also notify the Senior Chemist
    pharma_types = (Branch.PHARMACEUTICAL, Branch.PHARMACEUTICAL_NR)
    if sample.sample_type in pharma_types:
        notify_branch_heads(
            sample.sample_type,
            f'Pharmaceutical Report Submitted: {sample.lab_number}',
            f'Analyst {assignment.chemist.full_name} has submitted a report '
            f'for test "{assignment.test_name}" on pharmaceutical sample '
            f'"{sample.sample_name}" (Lab# {sample.lab_number}). '
            f'Senior Chemist review required.',
            f'/samples/assignment/{assignment.id}',
            exclude_user_id=sample.uploaded_by,
        )


def notify_preliminary_review_completed(assignment, action):
    """Called when an Officer completes preliminary review.
    
    Notifies:
    1. The chemist (when report is returned for correction)
    2. Senior Chemists/branch heads (when report is approved)
    3. The reviewing officer (always - confirmation of their action)
    """
    sample = assignment.sample
    action_text = 'approved' if action == 'approved' else 'returned for correction'
    link = f'/samples/assignment/{assignment.id}'

    # Notify the chemist when they need to resubmit (report returned)
    if action != 'approved':
        title = f'Preliminary Review – {action_text.title()}: {sample.lab_number}'
        message = (
            f'Your report for test "{assignment.test_name}" on sample '
            f'"{sample.sample_name}" (Lab# {sample.lab_number}) has been '
            f'{action_text} during preliminary review.'
        )
        if assignment.preliminary_review_comments:
            message += f'\n\nComments: {assignment.preliminary_review_comments}'
        create_notification(assignment.chemist_id, title, message, link)

    # If approved, notify Senior Chemist for Senior Chemist review
    if action == 'approved':
        notify_branch_heads(
            sample.sample_type,
            f'Report Ready for Senior Chemist Review: {sample.lab_number}',
            f'Report for test "{assignment.test_name}" on sample '
            f'"{sample.sample_name}" (Lab# {sample.lab_number}) '
            f'has passed preliminary review and is ready for '
            f'Senior Chemist review.',
            link,
        )

    # Notify the reviewing officer of the outcome (confirmation of action)
    reviewer = db.session.get(User, assignment.preliminary_reviewed_by)
    if reviewer:
        if action == 'approved':
            officer_title = f'✓ Preliminary Review Completed: {sample.lab_number}'
            officer_message = (
                f'You have approved the preliminary review for test '
                f'"{assignment.test_name}" on sample "{sample.sample_name}" '
                f'(Lab# {sample.lab_number}). The report has been forwarded '
                f'to the Senior Chemist for technical review.'
            )
        else:  # returned
            officer_title = f'⟲ Preliminary Review Returned: {sample.lab_number}'
            officer_message = (
                f'You have returned the report for test "{assignment.test_name}" '
                f'on sample "{sample.sample_name}" (Lab# {sample.lab_number}) '
                f'for correction. The analyst has been notified.'
            )
            if assignment.preliminary_review_comments:
                officer_message += f'\n\nYour Comments: {assignment.preliminary_review_comments}'

        create_notification(
            reviewer.id,
            officer_title,
            officer_message,
            link,
        )


def notify_report_reviewed(assignment, action):
    """Called when a Senior Chemist completes Senior Chemist review."""
    sample = assignment.sample
    action_text = {
        'accepted': 'accepted',
        'rejected': 'rejected',
        'returned': 'returned for correction',
    }.get(action, action)

    title = f'Senior Chemist Review – {action_text.title()}: {sample.lab_number}'
    link = f'/samples/assignment/{assignment.id}'

    # Only notify the chemist when they need to resubmit (report returned)
    if action == 'returned':
        message = (
            f'Your report for test "{assignment.test_name}" on sample '
            f'"{sample.sample_name}" (Lab# {sample.lab_number}) has been '
            f'{action_text} during Senior Chemist review.'
        )
        if assignment.review_comments:
            message += f'\n\nComments: {assignment.review_comments}'
        create_notification(assignment.chemist_id, title, message, link)

    # Also notify the uploading officer
    create_notification(
        sample.uploaded_by,
        title,
        f'Report for sample "{sample.sample_name}" '
        f'(Lab# {sample.lab_number}) has been {action_text} '
        f'during Senior Chemist review.',
        link,
    )

    # If all assignments accepted, notify Senior Chemist to submit to Deputy
    if action == 'accepted':
        from app.models import AssignmentStatus
        all_accepted = all(
            a.status in (AssignmentStatus.ACCEPTED, AssignmentStatus.COMPLETED)
            for a in sample.assignments.all()
        )
        if all_accepted:
            sample_link = f'/samples/{sample.id}'
            notify_branch_heads(
                sample.sample_type,
                f'All Reports Accepted: {sample.lab_number}',
                f'All analyst reports for sample "{sample.sample_name}" '
                f'(Lab# {sample.lab_number}) have been accepted. '
                f'Please prepare submission for the Deputy Government '
                f'Chemist.',
                sample_link,
            )


def notify_submitted_to_deputy(sample):
    """Called when Senior Chemist submits reports to Deputy."""
    title = f'Reports Submitted for Review: {sample.lab_number}'
    message = (
        f'Reports for sample "{sample.sample_name}" '
        f'(Lab# {sample.lab_number}) have been submitted for '
        f'review by the Deputy Government Chemist.'
    )
    link = f'/samples/{sample.id}'
    # Notify Deputy and HOD
    from app.models import Role
    deputies = User.query.join(user_roles).filter(
        user_roles.c.role.in_([Role.DEPUTY, Role.HOD]),
        User.is_active_user.is_(True),
    ).distinct().all()
    for user in deputies:
        create_notification(user.id, title, message, link)


def notify_deputy_review_completed(sample, action):
    """Called when Deputy completes review."""
    action_text = {
        'approved': 'accepted',
        'returned': 'returned to Senior Chemist',
        'rejected': 'rejected',
    }.get(action, action)
    title = f'Deputy Review – {action_text.title()}: {sample.lab_number}'
    message = (
        f'Sample "{sample.sample_name}" (Lab# {sample.lab_number}) '
        f'has been {action_text} by the Deputy Government Chemist.'
    )
    if sample.deputy_review_comments:
        message += f'\n\nComments: {sample.deputy_review_comments}'
    link = f'/samples/{sample.id}'

    # Notify branch heads (Senior Chemist)
    notify_branch_heads(sample.sample_type, title, message, link)

    # Notify the uploading officer
    create_notification(sample.uploaded_by, title, message, link)


def notify_certificate_prepared(sample):
    """Called when Deputy prepares Certificate of Analysis."""
    title = f'Certificate Ready for Review: {sample.lab_number}'
    message = (
        f'Certificate of Analysis for sample "{sample.sample_name}" '
        f'(Lab# {sample.lab_number}) has been prepared and is '
        f'awaiting review and signing by the Government Chemist.'
    )
    link = f'/samples/{sample.id}'
    # Notify HOD
    from app.models import Role
    hods = User.query.join(user_roles).filter(
        user_roles.c.role == Role.HOD,
        User.is_active_user.is_(True),
    ).distinct().all()
    for hod in hods:
        create_notification(hod.id, title, message, link)


def notify_certificate_signed(sample, action):
    """Called when Government Chemist reviews the certificate."""
    if action == 'sign':
        title = f'Certificate Signed: {sample.lab_number}'
        message = (
            f'Certificate of Analysis for sample "{sample.sample_name}" '
            f'(Lab# {sample.lab_number}) has been signed by the '
            f'Government Chemist. The sample analysis process is complete.'
        )
    else:  # returned
        title = f'Certificate Returned: {sample.lab_number}'
        message = (
            f'Certificate of Analysis for sample "{sample.sample_name}" '
            f'(Lab# {sample.lab_number}) has been returned by the '
            f'Government Chemist for correction.'
        )
        if sample.hod_review_comments:
            message += f'\n\nComments: {sample.hod_review_comments}'

    link = f'/samples/{sample.id}'

    # Notify Deputy, branch heads, and the uploading officer
    from app.models import Role
    deputies = User.query.join(user_roles).filter(
        user_roles.c.role == Role.DEPUTY,
        User.is_active_user.is_(True),
    ).distinct().all()
    for dep in deputies:
        create_notification(dep.id, title, message, link)

    notify_branch_heads(sample.sample_type, title, message, link)
    create_notification(sample.uploaded_by, title, message, link)


def notify_marked_out_of_spec(sample):
    """Called when a certified sample is flagged as out-of-spec after signing."""
    title = f'Sample Marked Out of Spec: {sample.lab_number}'
    message = (
        f'Certified sample "{sample.sample_name}" (Lab# {sample.lab_number}) '
        f'has been marked as OUT OF SPECIFICATION by '
        f'{sample.oos_marker.full_name if sample.oos_marker else "a reviewer"}.'
    )
    if sample.oos_mark_reason:
        message += f'\n\nReason: {sample.oos_mark_reason}'
    link = f'/samples/{sample.id}'

    notify_branch_heads(sample.sample_type, title, message, link,
                         exclude_user_id=sample.marked_oos_by)
    create_notification(sample.uploaded_by, title, message, link)


def notify_assignment_removed(chemist_id, sample_ref, test_name, removed_by, sample_id):
    """Notify the removed assignee when an assignment is removed."""
    from app.models import jamaica_now
    timestamp = jamaica_now().strftime('%d %b %Y %H:%M')
    title = f'Assignment Removed: {sample_ref}'
    message = (
        f'Your assignment for test "{test_name}" on sample '
        f'{sample_ref} has been removed by {removed_by}.\n\n'
        f'Removed at: {timestamp}'
    )
    link = f'/samples/{sample_id}'
    create_notification(chemist_id, title, message, link)


def notify_backdate_request_submitted(backdate_request):
    """Notify HOD and Deputy when a back-date request is submitted."""
    sample = backdate_request.sample
    requester = backdate_request.requester
    field_label = backdate_request.field_name.replace('_', ' ').title()
    title = f'Back-Date Request: {sample.lab_number}'
    message = (
        f'{requester.full_name} has requested to change {field_label} for '
        f'sample "{sample.sample_name}" '
        f'(Lab# {sample.lab_number}) from {backdate_request.original_date} '
        f'to {backdate_request.proposed_date}.\n\n'
        f'Reason: {backdate_request.reason or "N/A"}\n\n'
        f'Please review and approve or deny this request.'
    )
    link = '/backdate-requests'
    # Notify HOD and Deputy users
    deputies = User.query.join(user_roles).filter(
        user_roles.c.role.in_([Role.DEPUTY, Role.HOD]),
        User.is_active_user.is_(True),
    ).distinct().all()
    for user in deputies:
        if user.id != requester.id:
            create_notification(user.id, title, message, link)


def notify_backdate_request_decided(backdate_request):
    """Notify the requester when a back-date request is approved or denied."""
    sample = backdate_request.sample
    decision = backdate_request.status  # 'approved' or 'denied'
    decider = backdate_request.decider
    field_label = backdate_request.field_name.replace('_', ' ').title()
    title = f'Back-Date Request {decision.title()}: {sample.lab_number}'
    message = (
        f'Your request to change {field_label} for sample '
        f'"{sample.sample_name}" (Lab# {sample.lab_number}) from '
        f'{backdate_request.original_date} to {backdate_request.proposed_date} '
        f'has been {decision} by {decider.full_name}.'
    )
    if backdate_request.decision_comments:
        message += f'\n\nComments: {backdate_request.decision_comments}'
    link = f'/samples/{sample.id}'
    create_notification(backdate_request.requested_by, title, message, link)


def notify_delete_request_submitted(delete_request):
    """Notify HOD users when a deletion request is submitted."""
    label = delete_request.entity_label or 'Unknown'
    requester = delete_request.requester
    if delete_request.request_type == 'sample':
        subject = f'Deletion Request (Sample): {label}'
        body = (
            f'{requester.full_name} has requested deletion of sample '
            f'"{label}".\n\n'
            f'Reason: {delete_request.reason or "N/A"}\n\n'
            f'Please review and approve or deny this request.'
        )
    else:
        subject = f'Deletion Request (Assignment): {label}'
        body = (
            f'{requester.full_name} has requested deletion of test assignment '
            f'"{label}".\n\n'
            f'Reason: {delete_request.reason or "N/A"}\n\n'
            f'Please review and approve or deny this request.'
        )
    link = '/delete-requests'
    hods = User.query.join(user_roles).filter(
        user_roles.c.role.in_([Role.HOD, Role.ADMIN]),
        User.is_active_user.is_(True),
    ).distinct().all()
    for user in hods:
        if user.id != requester.id:
            create_notification(user.id, subject, body, link)


def notify_delete_request_decided(delete_request):
    """Notify the requester when their deletion request is decided."""
    decision = delete_request.status  # 'approved' or 'denied'
    decider = delete_request.decider
    label = delete_request.entity_label or 'Unknown'
    decider_name = decider.full_name if decider else 'Administrator'
    title = f'Deletion Request {decision.title()}: {label}'
    if delete_request.request_type == 'sample':
        message = (
            f'Your request to delete sample "{label}" has been '
            f'{decision} by {decider_name}.'
        )
    else:
        message = (
            f'Your request to delete test assignment "{label}" has been '
            f'{decision} by {decider_name}.'
        )
    if delete_request.decision_comments:
        message += f'\n\nComments: {delete_request.decision_comments}'
    # If approved the entity may no longer exist, link to sample list instead
    if delete_request.request_type == 'sample' and delete_request.sample_id:
        link = f'/samples/{delete_request.sample_id}'
    elif delete_request.request_type == 'assignment' and delete_request.sample:
        link = f'/samples/{delete_request.sample.id}'
    else:
        link = '/samples/'
    create_notification(delete_request.requested_by, title, message, link)


# ---------------------------------------------------------------------------
# Expected Report Date Reminders
# ---------------------------------------------------------------------------

def send_report_date_reminders():
    """Check samples with approaching or past expected report dates and notify
    the assigned chemist(s), the uploading officer, and branch heads.

    Sends reminders at 3 days before, 1 day before, and on the due date.
    Also sends a daily overdue notification for samples past their due date.
    Skips samples that are already certified/completed/rejected.
    Avoids duplicate notifications by checking existing reminders for the
    same sample and reminder tier on the same day.
    """
    from app.models import Sample, SampleStatus, SampleAssignment

    today = date.today()
    thresholds = [
        (3, 'in 3 days'),
        (1, 'tomorrow'),
        (0, 'today'),
    ]

    terminal_statuses = {
        SampleStatus.CERTIFIED,
        SampleStatus.COMPLETED,
        SampleStatus.REJECTED,
    }

    # Find samples with expected_report_date within the reminder window
    window_start = today
    window_end = today + timedelta(days=3)
    samples = Sample.query.filter(
        Sample.expected_report_date.isnot(None),
        Sample.expected_report_date >= window_start,
        Sample.expected_report_date <= window_end,
        Sample.status.notin_(terminal_statuses),
    ).all()

    count = 0
    for sample in samples:
        days_remaining = (sample.expected_report_date - today).days
        # Match the closest threshold
        matched = None
        for days, label in thresholds:
            if days_remaining == days:
                matched = (days, label)
                break
        if matched is None:
            continue

        days_val, label = matched

        # Skip if a reminder was already sent today for this tier
        existing = Notification.query.filter(
            Notification.title.contains(f'Report Due {label.title()}'),
            Notification.message.contains(sample.lab_number),
            Notification.created_at >= db.func.date(db.func.current_timestamp()),
        ).first()
        if existing:
            continue

        if days_val == 0:
            urgency = '🔴'
            title = f'{urgency} Report Due Today: {sample.lab_number}'
            message = (
                f'The expected report date for sample "{sample.sample_name}" '
                f'(Lab# {sample.lab_number}) is TODAY. '
                f'Please ensure the report is submitted promptly.'
            )
        elif days_val == 1:
            urgency = '🟠'
            title = f'{urgency} Report Due Tomorrow: {sample.lab_number}'
            message = (
                f'The expected report date for sample "{sample.sample_name}" '
                f'(Lab# {sample.lab_number}) is tomorrow '
                f'({sample.expected_report_date.strftime("%d %b %Y")}). '
                f'Please ensure the report is on track.'
            )
        else:
            urgency = '🟡'
            title = f'{urgency} Report Due In 3 Days: {sample.lab_number}'
            message = (
                f'The expected report date for sample "{sample.sample_name}" '
                f'(Lab# {sample.lab_number}) is in 3 days '
                f'({sample.expected_report_date.strftime("%d %b %Y")}). '
                f'Please review progress.'
            )

        link = f'/samples/{sample.id}'
        notified_users = set()

        # Notify assigned chemists
        for assignment in sample.assignments.all():
            if assignment.chemist_id not in notified_users:
                create_notification(assignment.chemist_id, title, message, link)
                notified_users.add(assignment.chemist_id)
                count += 1

        # Notify the officer who registered the sample
        if sample.uploaded_by not in notified_users:
            create_notification(sample.uploaded_by, title, message, link)
            notified_users.add(sample.uploaded_by)
            count += 1

        # Notify branch heads
        head_roles = [Role.SENIOR_CHEMIST, Role.DEPUTY, Role.HOD]
        heads = User.query.join(user_roles).filter(
            user_roles.c.role.in_(head_roles),
            User.is_active_user.is_(True),
        ).distinct().all()

        effective_branch = sample.sample_type
        if effective_branch == Branch.PHARMACEUTICAL_NR:
            effective_branch = Branch.PHARMACEUTICAL

        for head in heads:
            if head.id in notified_users:
                continue
            if head.branches and effective_branch not in head.branches and sample.sample_type not in head.branches:
                continue
            create_notification(head.id, title, message, link)
            notified_users.add(head.id)
            count += 1

    # -----------------------------------------------------------------------
    # Overdue samples: expected_report_date is in the past and not terminal
    # -----------------------------------------------------------------------
    overdue_samples = Sample.query.filter(
        Sample.expected_report_date.isnot(None),
        Sample.expected_report_date < today,
        Sample.status.notin_(terminal_statuses),
    ).all()

    for sample in overdue_samples:
        overdue_days = (today - sample.expected_report_date).days
        title = f'🔴 OVERDUE: {sample.lab_number}'
        message = (
            f'Sample "{sample.sample_name}" (Lab# {sample.lab_number}) '
            f'is OVERDUE by {overdue_days} day{"s" if overdue_days != 1 else ""}. '
            f'Expected report date was {sample.expected_report_date.strftime("%d %b %Y")}. '
            f'Please take immediate action.'
        )
        link = f'/samples/{sample.id}'

        # Avoid sending duplicate overdue notifications on the same day
        existing = Notification.query.filter(
            Notification.title.contains('OVERDUE'),
            Notification.title.contains(sample.lab_number),
            Notification.created_at >= db.func.date(db.func.current_timestamp()),
        ).first()
        if existing:
            continue

        notified_users = set()

        for assignment in sample.assignments.all():
            if assignment.chemist_id not in notified_users:
                create_notification(assignment.chemist_id, title, message, link)
                notified_users.add(assignment.chemist_id)
                count += 1

        if sample.uploaded_by not in notified_users:
            create_notification(sample.uploaded_by, title, message, link)
            notified_users.add(sample.uploaded_by)
            count += 1

        head_roles = [Role.SENIOR_CHEMIST, Role.DEPUTY, Role.HOD]
        heads = User.query.join(user_roles).filter(
            user_roles.c.role.in_(head_roles),
            User.is_active_user.is_(True),
        ).distinct().all()

        effective_branch = sample.sample_type
        if effective_branch == Branch.PHARMACEUTICAL_NR:
            effective_branch = Branch.PHARMACEUTICAL

        for head in heads:
            if head.id in notified_users:
                continue
            if head.branches and effective_branch not in head.branches and sample.sample_type not in head.branches:
                continue
            create_notification(head.id, title, message, link)
            notified_users.add(head.id)
            count += 1

    if count:
        db.session.commit()
        current_app.logger.info(
            f'Report date reminders: sent {count} notification(s).'
        )
    return count
