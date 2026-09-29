import json
import os
import uuid
from datetime import datetime, timezone, date, timedelta

from flask import (
    render_template, redirect, url_for, flash, request,
    current_app, send_from_directory, abort, jsonify,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app import db
from app.samples import samples_bp
from app.models import (
    Sample, SampleAssignment, SampleHistory, User, SupportingDocument,
    SampleImage,
    Role, Branch, SampleStatus, AssignmentStatus, Permission,
    user_roles, user_branches, jamaica_now,
    DocumentVersion, ReviewHistory, BackDateRequest,
    AuditLog, Notification, Setting, DeleteRequest,
    KpiTarget, fiscal_year_for_date, fiscal_quarter_for_date,
    calculate_working_days, fetch_non_working_days, add_working_days,
    Invoice, InvoiceItem, PHARMA_TEST_PRICES, DropdownConfig,
    SampleComment,
)
from app.forms import (
    SampleRegisterForm, SampleEditForm, SampleAssignForm,
    ReportSubmitForm, PreliminaryReviewForm, ReportReviewForm,
    SubmitToDeputyForm, DeputyReviewForm, CertificateForm, HODReviewForm,
    get_sample_register_form, SupportingDocumentForm, BackDateRequestForm,
    SampleImageForm,
    DeleteRequestForm, COADecertifyForm, COAReissueForm,
    InvoiceCreateForm, InvoiceItemForm,
    MarkOutOfSpecForm,
    BRANCH_TEST_NAMES, BRANCH_TEST_REFERENCES,
)
from app.notifications import (
    notify_sample_uploaded, notify_sample_assigned,
    notify_report_submitted, notify_preliminary_review_completed,
    notify_report_reviewed, notify_submitted_to_deputy,
    notify_deputy_review_completed, notify_certificate_prepared,
    notify_certificate_signed, notify_assignment_removed,
    notify_backdate_request_submitted,
    notify_delete_request_submitted,
    notify_marked_out_of_spec,
)


def _save_file(file_storage):
    """Save an uploaded file and return (stored_name, original_name).

    Aborts with 400 if the file extension is not in ALLOWED_EXTENSIONS.
    """
    original = secure_filename(file_storage.filename)
    ext = original.rsplit('.', 1)[-1].lower() if '.' in original else ''
    if not ext:
        abort(400, description='Files without an extension are not permitted.')
    if ext not in current_app.config['ALLOWED_EXTENSIONS']:
        abort(400, description=f'File type ".{ext}" is not permitted.')
    stored = f'{uuid.uuid4().hex}.{ext}'
    file_storage.save(
        os.path.join(current_app.config['UPLOAD_FOLDER'], stored)
    )
    return stored, original


from wtforms import SelectMultipleField as _SelectMultipleField

_DROPDOWN_FIELD_CATEGORIES = [
    ('formulation_type', 'formulation_type', '-- Select Formulation --'),
    ('active_ingredient', 'api', '-- Select API --'),
    ('toxicology_sample_type_name', 'toxicology_sample_type', '-- Select Sample Type --'),
]


def _apply_dropdown_choices(form):
    """Override SelectField choices from DropdownConfig if DB entries exist.

    Falls back to hardcoded defaults when no active entries are configured for
    a category, so existing behaviour is preserved until the admin configures
    the dropdown.
    """
    for field_name, category, blank_label in _DROPDOWN_FIELD_CATEGORIES:
        field = getattr(form, field_name, None)
        if field is None:
            continue
        db_choices = DropdownConfig.choices_for(category)
        if db_choices:
            if isinstance(field, _SelectMultipleField):
                field.choices = db_choices
            else:
                field.choices = [('', blank_label)] + db_choices


def _get_field(form, name):
    """Return the value of an optional form field, or None if absent/empty."""
    field = getattr(form, name, None)
    if field is None:
        return None
    return field.data or None


def _serialize_apis(value):
    """Convert a list of API strings (from SelectMultipleField) or a plain string
    to a comma-separated string suitable for storage, or None if empty.
    """
    if isinstance(value, list):
        joined = ', '.join(v for v in value if v)
        return joined or None
    return value or None


def _add_history(sample, action, details=None, action_type=None,
                 object_affected=None, change_description=None):
    entry = SampleHistory(
        sample_id=sample.id,
        action=action,
        details=details,
        performed_by=current_user.id,
        action_type=action_type or action,
        object_affected=object_affected,
        change_description=change_description,
    )
    db.session.add(entry)


def _assignments_ready_for_deputy(sample):
    """Return True when every assignment is in an accepted/completed state."""
    assignments = sample.assignments.all()
    return bool(assignments) and all(
        a.status in (AssignmentStatus.ACCEPTED, AssignmentStatus.COMPLETED)
        for a in assignments
    )


def _can_submit_to_deputy(sample):
    """Return True when a sample is valid for Deputy submission/resubmission."""
    # Statuses that are already at or beyond the Deputy review stage should
    # never trigger a (re-)submission to Deputy.
    post_deputy_statuses = {
        SampleStatus.DEPUTY_REVIEW,
        SampleStatus.CERTIFICATE_PREPARATION,
        SampleStatus.HOD_REVIEW,
        SampleStatus.HOD_RETURNED,
        SampleStatus.CERTIFIED,
        SampleStatus.REJECTED,
        SampleStatus.COMPLETED,
    }
    if sample.status in post_deputy_statuses:
        return False
    return (
        sample.status in (SampleStatus.ACCEPTED, SampleStatus.DEPUTY_RETURNED)
        or _assignments_ready_for_deputy(sample)
    )


def _can_show_submit_to_deputy(sample):
    """Return True when the detail page should show the primary submit action."""
    return _can_submit_to_deputy(sample) and sample.status != SampleStatus.DEPUTY_RETURNED


# ---------------------------------------------------------------------------
# List / Dashboard views
# ---------------------------------------------------------------------------

@samples_bp.route('/')
@login_required
def sample_list():
    query = Sample.query

    status_filters = request.args.getlist('status')
    # Basic filters
    status_filter = request.args.get('status')
    type_filter = request.args.get('type')
    search = request.args.get('q', '').strip()
    # pending_prelim=1 shows samples with ≥1 assignment at REPORT_SUBMITTED,
    # regardless of the overall Sample.status (which may have been superseded by
    # a higher-priority assignment state, e.g. UNDER_TECHNICAL_REVIEW).
    pending_prelim = request.args.get('pending_prelim') == '1'
    sort_by = request.args.get('sort', 'date_received')
    sort_dir = request.args.get('dir', 'desc').lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    if not per_page or per_page < 1:
        per_page = 25

    # Advanced filters
    adv_sample_name = request.args.get('sample_name', '').strip()
    adv_formulation = request.args.get('formulation_type', '').strip()
    adv_manufacturer = request.args.get('manufacturer', '').strip()
    adv_api = request.args.get('api', '').strip()
    adv_source = request.args.get('source', '').strip()
    adv_parish = request.args.get('parish', '').strip()
    adv_milk_type = request.args.get('milk_type', '').strip()
    adv_hospital = request.args.get('hospital', '').strip()
    adv_tox_type = request.args.get('tox_sample_type', '').strip()
    adv_patient_name = request.args.get('patient_name', '').strip()
    adv_alcohol_type = request.args.get('alcohol_type', '').strip()

    if len(status_filters) > 1:
        valid_statuses = []
        for sf in status_filters:
            try:
                valid_statuses.append(SampleStatus[sf])
            except KeyError:
                pass
        if valid_statuses:
            query = query.filter(Sample.status.in_(valid_statuses))
    elif status_filter:
        try:
            query = query.filter(Sample.status == SampleStatus[status_filter])
        except KeyError:
            pass
    if type_filter:
        try:
            query = query.filter(Sample.sample_type == Branch[type_filter])
        except KeyError:
            pass
    if search:
        query = query.filter(
            db.or_(
                Sample.lab_number.ilike(f'%{search}%'),
                Sample.sample_name.ilike(f'%{search}%'),
            )
        )

    # Apply advanced filters
    if adv_sample_name:
        query = query.filter(Sample.sample_name.ilike(f'%{adv_sample_name}%'))
    if adv_formulation:
        query = query.filter(Sample.formulation_type.ilike(f'%{adv_formulation}%'))
    if adv_manufacturer:
        query = query.filter(Sample.manufacturer.ilike(f'%{adv_manufacturer}%'))
    if adv_api:
        query = query.filter(Sample.api.ilike(f'%{adv_api}%'))
    if adv_source:
        query = query.filter(Sample.source.ilike(f'%{adv_source}%'))
    if adv_parish:
        query = query.filter(Sample.parish.ilike(f'%{adv_parish}%'))
    if adv_milk_type in ('R', 'P'):
        query = query.filter(Sample.milk_type == adv_milk_type)
    if adv_hospital:
        query = query.filter(Sample.source.ilike(f'%{adv_hospital}%'))
    if adv_tox_type:
        query = query.filter(
            Sample.toxicology_sample_type_name.ilike(f'%{adv_tox_type}%')
        )
    if adv_patient_name:
        query = query.filter(Sample.patient_name.ilike(f'%{adv_patient_name}%'))
    if adv_alcohol_type:
        query = query.filter(Sample.alcohol_type.ilike(f'%{adv_alcohol_type}%'))

    # pending_prelim: include any sample that has at least one assignment still
    # at REPORT_SUBMITTED — catches the case where one test has already moved on
    # to UNDER_TECHNICAL_REVIEW (which overrides Sample.status) while another
    # test for the same sample is still waiting for preliminary review.
    if pending_prelim:
        _prelim_ids = db.select(SampleAssignment.sample_id).where(
            SampleAssignment.status == AssignmentStatus.REPORT_SUBMITTED
        ).distinct().scalar_subquery()
        query = query.filter(Sample.id.in_(_prelim_ids))

    # Role-based filtering
    # Officers, Deputies, HOD, and Admins see all samples (no filter).
    # Restrictive scoping is driven by PRIMARY roles only: acting roles are
    # strictly additive and must never narrow what a user could already see.
    if current_user.has_primary_role(Role.CHEMIST) and not current_user.has_any_role(Role.OFFICER, Role.SENIOR_CHEMIST, Role.DEPUTY, Role.HOD, Role.ADMIN):
        # Chemists see only samples assigned to them
        assigned_ids = db.select(SampleAssignment.sample_id).where(
            SampleAssignment.chemist_id == current_user.id
        ).scalar_subquery()
        query = query.filter(Sample.id.in_(assigned_ids))
    elif current_user.has_primary_role(Role.SENIOR_CHEMIST) and current_user.branches and not current_user.has_any_role(Role.OFFICER, Role.DEPUTY, Role.HOD, Role.ADMIN):
        # Senior Chemists see samples in their branch(es)
        query = query.filter(Sample.sample_type.in_(current_user.branches))

    sort_columns = {
        'lab_number': Sample.lab_number,
        'sample_name': Sample.sample_name,
        'date_received': Sample.date_received,
        'status': Sample.status,
    }
    sort_col = sort_columns.get(sort_by, Sample.date_received)
    order_clause = sort_col.asc() if sort_dir == 'asc' else sort_col.desc()

    result_count = query.count()
    pagination = query.order_by(order_clause).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Working-day remaining TAT by row for list badges
    today = date.today()
    tat_remaining = {}
    horizon_start = today - timedelta(days=365)
    horizon_end = today + timedelta(days=365)
    non_working = fetch_non_working_days(horizon_start, horizon_end)
    for s in pagination.items:
        if not s.expected_report_date:
            tat_remaining[s.id] = None
            continue
        if s.expected_report_date == today:
            tat_remaining[s.id] = 0
            continue
        if s.expected_report_date > today:
            tat_remaining[s.id] = calculate_working_days(
                today, s.expected_report_date, non_working
            )
        else:
            tat_remaining[s.id] = -calculate_working_days(
                s.expected_report_date, today, non_working
            )

    adv_filters = {
        'sample_name': adv_sample_name,
        'formulation_type': adv_formulation,
        'manufacturer': adv_manufacturer,
        'api': adv_api,
        'source': adv_source,
        'parish': adv_parish,
        'milk_type': adv_milk_type,
        'hospital': adv_hospital,
        'tox_sample_type': adv_tox_type,
        'patient_name': adv_patient_name,
        'alcohol_type': adv_alcohol_type,
    }
    adv_active = any(adv_filters.values())

    return render_template(
        'samples/sample_list.html',
        samples=pagination.items,
        pagination=pagination,
        SampleStatus=SampleStatus,
        Branch=Branch,
        sort_by=sort_by,
        sort_dir=sort_dir,
        tat_remaining=tat_remaining,
        status_filter=status_filter,
        status_filters=status_filters,
        type_filter=type_filter,
        search=search,
        adv=adv_filters,
        adv_active=adv_active,
        pending_prelim=pending_prelim,
        result_count=result_count,
        is_filtered=bool(status_filter or type_filter or search or adv_active or pending_prelim),
        today_date=date.today(),
    )


# ---------------------------------------------------------------------------
# Register a new sample
# ---------------------------------------------------------------------------

def _generate_lab_number(branch):
    """Auto-generate a unique lab number for pharmaceutical samples."""
    from datetime import date
    prefix_map = {
        Branch.PHARMACEUTICAL: 'PH',
        Branch.PHARMACEUTICAL_NR: 'PNR',
    }
    prefix = prefix_map.get(branch, 'LAB')
    year = date.today().year
    # Find the latest number with this prefix+year
    pattern = f'{prefix}{year}%'
    last = Sample.query.filter(
        Sample.lab_number.like(pattern)
    ).order_by(Sample.id.desc()).first()
    if last:
        try:
            seq = int(last.lab_number[len(prefix) + 4:]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f'{prefix}{year}{seq:04d}'


@samples_bp.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if not (
        current_user.has_any_role(Role.OFFICER, Role.ADMIN, Role.HOD)
        or current_user.has_permission(Permission.REGISTER_SAMPLE)
    ):
        flash('Only officers can register samples.', 'danger')
        return redirect(url_for('samples.sample_list'))

    selected_type = request.form.get('sample_type') if request.method == 'POST' else request.args.get('type')
    if not selected_type:
        selected_type = next(iter(Branch)).name
    FormClass = get_sample_register_form(selected_type)
    form = FormClass()
    _apply_dropdown_choices(form)

    if request.method == 'GET' and selected_type in Branch.__members__:
        form.sample_type.data = selected_type

    is_pharma = selected_type in ('PHARMACEUTICAL', 'PHARMACEUTICAL_NR')

    if form.validate_on_submit():
        branch = Branch[form.sample_type.data]
        is_pharma_type = branch in (Branch.PHARMACEUTICAL, Branch.PHARMACEUTICAL_NR)

        # Auto-generate lab number for pharmaceutical samples if not provided
        lab_number = form.lab_number.data if hasattr(form, 'lab_number') and form.lab_number.data else None
        if not lab_number and is_pharma_type:
            lab_number = _generate_lab_number(branch)
        elif not lab_number:
            flash('Lab number is required.', 'danger')
            return render_template('samples/register.html', form=form, is_pharma=is_pharma)

        # Date received is always manually entered
        date_received = form.date_received.data

        sample = Sample(
            lab_number=lab_number,
            sample_name=form.sample_name.data,
            sample_type=branch,
            description=form.description.data,
            quantity=_get_field(form, 'quantity'),
            parish=_get_field(form, 'parish'),
            patient_name=_get_field(form, 'patient_name'),
            source=_get_field(form, 'source'),
            date_received=date_received,
            expected_report_date=_get_field(form, 'expected_report_date'),
            uploaded_by=current_user.id,
        )

        # Type-specific fields
        vol = _get_field(form, 'volume')
        if vol:
            sample.volume = vol
        mt = _get_field(form, 'milk_type')
        if mt:
            sample.milk_type = mt
        ft = _get_field(form, 'formulation_type')
        if ft:
            sample.formulation_type = ft
        mfr = _get_field(form, 'manufacturer')
        if mfr:
            sample.manufacturer = mfr
        api_val = _get_field(form, 'api')
        if api_val:
            sample.api = api_val
        at = _get_field(form, 'alcohol_type')
        if at:
            sample.alcohol_type = at
        cb = _get_field(form, 'claim_butt_number')
        if cb:
            sample.claim_butt_number = cb
        # New fields
        tst = _get_field(form, 'toxicology_sample_type_name')
        if tst:
            sample.toxicology_sample_type_name = tst
        ln = _get_field(form, 'lot_number')
        if ln:
            sample.lot_number = ln
        ed = _get_field(form, 'expiration_date')
        if ed:
            sample.expiration_date = ed
        bln = _get_field(form, 'batch_lot_number')
        if bln:
            sample.batch_lot_number = bln
        # Toxicology clinical fields
        dn = _get_field(form, 'doctors_name')
        if dn:
            sample.doctors_name = dn
        rdn = _get_field(form, 'registration_docket_no')
        if rdn:
            sample.registration_docket_no = rdn
        pg = _get_field(form, 'patient_gender')
        if pg:
            sample.patient_gender = pg
        wc = _get_field(form, 'ward_clinic')
        if wc:
            sample.ward_clinic = wc
        tr = _get_field(form, 'test_requested')
        if tr:
            sample.test_requested = tr
        di = _get_field(form, 'diagnosis_indicated')
        if di:
            sample.diagnosis_indicated = di

        if form.scanned_file.data:
            stored, original = _save_file(form.scanned_file.data)
            sample.scanned_file = stored
            sample.scanned_file_original_name = original

        db.session.add(sample)
        try:
            db.session.flush()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Failed to flush new sample %r', lab_number)
            flash(f'An error occurred while registering the sample: {exc}', 'danger')
            return render_template('samples/register.html', form=form, is_pharma=is_pharma)

        # Create document version for scanned file
        if form.scanned_file.data:
            db.session.add(DocumentVersion(
                sample_id=sample.id,
                document_type='scanned_file',
                version_number=1,
                file_path=sample.scanned_file,
                original_name=sample.scanned_file_original_name,
                upload_label='original',
                uploaded_by=current_user.id,
            ))

        _add_history(sample, 'Sample Registered',
                     f'Registered by {current_user.full_name}',
                     action_type='Original Submission',
                     object_affected='Sample')
        db.session.add(AuditLog(
            action='SAMPLE_REGISTERED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'sample_name': sample.sample_name,
                'sample_type': sample.sample_type.value,
                'date_received': sample.date_received.isoformat() if sample.date_received else None,
                'registered_by': current_user.full_name,
            }),
            performed_by=current_user.id,
            performed_at=jamaica_now(),
        ))
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Failed to commit new sample %r', lab_number)
            flash(f'An error occurred while registering the sample: {exc}', 'danger')
            return render_template('samples/register.html', form=form, is_pharma=is_pharma)

        notify_sample_uploaded(sample)
        db.session.commit()

        flash(f'Sample {sample.lab_number} registered successfully.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    return render_template('samples/register.html', form=form, is_pharma=is_pharma)


# ---------------------------------------------------------------------------
# Sample detail
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>')
@login_required
def detail(sample_id):
    sample = db.get_or_404(Sample, sample_id)
    assignments = sample.assignments.all()
    can_submit_to_deputy = _can_show_submit_to_deputy(sample)
    review_page = request.args.get('review_page', 1, type=int)
    activity_page = request.args.get('activity_page', 1, type=int)
    history_pagination = sample.history.paginate(
        page=activity_page, per_page=10, error_out=False
    )
    supporting_docs = sample.supporting_documents.order_by(
        SupportingDocument.uploaded_at.desc()
    ).all()
    supporting_doc_form = SupportingDocumentForm()
    sample_images = sample.images.order_by(
        SampleImage.uploaded_at.desc()
    ).all()
    sample_image_form = SampleImageForm()
    document_versions = sample.document_versions.order_by(
        DocumentVersion.document_type, DocumentVersion.version_number.desc()
    ).all() if hasattr(sample, 'document_versions') else []
    review_pagination = ReviewHistory.query.filter_by(
        sample_id=sample.id
    ).order_by(ReviewHistory.reviewed_at.desc()).paginate(
        page=review_page, per_page=10, error_out=False
    )
    pending_backdate = BackDateRequest.query.filter_by(
        sample_id=sample.id, field_name='date_registered', status='pending',
    ).first()
    return render_template(
        'samples/detail.html',
        sample=sample,
        assignments=assignments,
        history=history_pagination.items,
        history_pagination=history_pagination,
        supporting_docs=supporting_docs,
        supporting_doc_form=supporting_doc_form,
        sample_images=sample_images,
        sample_image_form=sample_image_form,
        today_date=date.today(),
        document_versions=document_versions,
        review_histories=review_pagination.items,
        review_pagination=review_pagination,
        pending_backdate=pending_backdate,
        can_submit_to_deputy=can_submit_to_deputy,
        SampleComment=SampleComment,
    )


# ---------------------------------------------------------------------------
# Edit sample
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(sample_id):
    sample = db.get_or_404(Sample, sample_id)
    can_edit = (
        current_user.has_any_role(Role.OFFICER, Role.ADMIN, Role.HOD, Role.SENIOR_CHEMIST)
        or current_user.has_permission(Permission.EDIT_SAMPLE)
        or current_user.id == sample.uploaded_by
    )
    if not can_edit:
        flash('Access denied.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = SampleEditForm(obj=sample)
    _apply_dropdown_choices(form)
    # Any user explicitly granted the Edit Accreditation permission (and
    # admins, who implicitly hold all permissions) may change the accreditation
    # status of a sample, whether or not one has already been set.
    can_edit_accreditation = current_user.has_permission(
        Permission.EDIT_ACCREDITATION
    )
    # Ensure the Laboratory dropdown is pre-selected with the current value.
    # obj=sample sets sample_type to the Branch enum, but the SelectField
    # expects the enum .name string to match its choices.
    if request.method == 'GET' and sample.sample_type:
        form.sample_type.data = sample.sample_type.name
        form.lab_number.data = sample.lab_number
        if sample.api:
            if isinstance(form.active_ingredient, _SelectMultipleField):
                form.active_ingredient.data = [
                    v.strip() for v in sample.api.split(',') if v.strip()
                ]
            else:
                form.active_ingredient.data = sample.api
    if request.method == 'GET' and sample.is_accredited is not None:
        form.accreditation.data = (
            'accredited' if sample.is_accredited else 'not_accredited'
        )
    if form.validate_on_submit():
        new_lab_number = form.lab_number.data.strip()
        if new_lab_number != sample.lab_number:
            conflict = Sample.query.filter(
                Sample.lab_number == new_lab_number,
                Sample.id != sample.id,
            ).first()
            if conflict:
                flash(f'Lab number "{new_lab_number}" is already in use by another sample.', 'danger')
                return render_template('samples/edit.html', form=form, sample=sample, can_edit_accreditation=can_edit_accreditation)
            sample.lab_number = new_lab_number
        sample.sample_name = form.sample_name.data
        sample.sample_type = Branch[form.sample_type.data]
        sample.description = form.description.data
        sample.quantity = form.quantity.data
        sample.volume = form.volume.data
        sample.parish = form.parish.data
        sample.patient_name = form.patient_name.data
        sample.source = form.source.data
        sample.formulation_type = form.formulation_type.data
        sample.manufacturer = form.manufacturer.data or None
        if isinstance(form.active_ingredient, _SelectMultipleField):
            sample.api = _serialize_apis(form.active_ingredient.data)
        else:
            sample.api = form.api.data or None
        sample.alcohol_type = form.alcohol_type.data if form.alcohol_type.data else None
        sample.claim_butt_number = form.claim_butt_number.data
        sample.batch_lot_number = form.batch_lot_number.data or None
        sample.milk_type = form.milk_type.data if form.milk_type.data else None
        sample.lot_number = form.lot_number.data or None
        sample.expiration_date = form.expiration_date.data
        sample.toxicology_sample_type_name = form.toxicology_sample_type_name.data or None
        sample.doctors_name = form.doctors_name.data or None
        sample.registration_docket_no = form.registration_docket_no.data or None
        sample.patient_gender = form.patient_gender.data or None
        sample.ward_clinic = form.ward_clinic.data or None
        sample.test_requested = form.test_requested.data or None
        sample.diagnosis_indicated = form.diagnosis_indicated.data or None
        sample.expected_report_date = form.expected_report_date.data

        # Update accreditation status only when the current user is authorised
        # to edit it.
        if can_edit_accreditation and form.accreditation.data:
            new_accredited = form.accreditation.data == 'accredited'
            if new_accredited != sample.is_accredited:
                sample.is_accredited = new_accredited
                _add_history(
                    sample, 'Accreditation Updated',
                    (f'Accreditation status changed to '
                     f'{"Accredited" if new_accredited else "Not Accredited"} '
                     f'by {current_user.full_name}'),
                    action_type='Edit', object_affected='Accreditation Status',
                )
                db.session.add(AuditLog(
                    action='ACCREDITATION_UPDATED',
                    entity_type='Sample',
                    entity_id=sample.id,
                    entity_label=sample.lab_number,
                    details=json.dumps({
                        'lab_number': sample.lab_number,
                        'is_accredited': new_accredited,
                        'updated_by': current_user.full_name,
                    }),
                    performed_by=current_user.id,
                    performed_at=jamaica_now(),
                ))

        scanned_upload = form.scanned_file.data
        if (
            scanned_upload
            and hasattr(scanned_upload, 'filename')
            and scanned_upload.filename
        ):
            stored, original = _save_file(scanned_upload)
            sample.scanned_file = stored
            sample.scanned_file_original_name = original

            # Create document version for replaced scanned file
            existing = DocumentVersion.query.filter_by(
                sample_id=sample.id, document_type='scanned_file'
            ).count()
            db.session.add(DocumentVersion(
                sample_id=sample.id,
                document_type='scanned_file',
                version_number=existing + 1,
                file_path=stored,
                original_name=original,
                upload_label='revised',
                uploaded_by=current_user.id,
            ))

        _add_history(sample, 'Sample Updated', f'Updated by {current_user.full_name}',
                     action_type='Edit', object_affected='Sample')
        db.session.add(AuditLog(
            action='SAMPLE_UPDATED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'sample_name': sample.sample_name,
                'sample_type': sample.sample_type.value,
                'updated_by': current_user.full_name,
            }),
            performed_by=current_user.id,
            performed_at=jamaica_now(),
        ))
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Failed to save edits for sample %r', sample.lab_number)
            flash(f'An error occurred while saving the sample: {exc}', 'danger')
            return render_template('samples/edit.html', form=form, sample=sample, can_edit_accreditation=can_edit_accreditation)
        flash('Sample updated.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    return render_template('samples/edit.html', form=form, sample=sample, can_edit_accreditation=can_edit_accreditation)


# ---------------------------------------------------------------------------
# Assign sample to chemist(s)
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/assign', methods=['GET', 'POST'])
@login_required
def assign(sample_id):
    sample = db.get_or_404(Sample, sample_id)
    if not (
        current_user.is_branch_head()
        or current_user.has_role(Role.ADMIN)
        or current_user.has_permission(Permission.ASSIGN_SAMPLE)
    ):
        flash('Only Senior Chemists / Branch Heads can assign samples.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = SampleAssignForm()

    # Determine if this sample type has predefined test names/references
    branch_key = sample.sample_type.name
    # Try dynamic dropdown config first, fall back to hardcoded lists
    dynamic_tests = DropdownConfig.choices_for('test_name', branch=sample.sample_type.value)
    predefined_tests = dynamic_tests if dynamic_tests else BRANCH_TEST_NAMES.get(branch_key)
    predefined_refs = BRANCH_TEST_REFERENCES.get(branch_key)
    has_predefined_tests = bool(predefined_tests)
    has_predefined_refs = bool(predefined_refs)

    if has_predefined_tests:
        form.test_names.choices = predefined_tests
    if has_predefined_refs:
        form.test_reference_select.choices = [('', '-- Select Reference --')] + predefined_refs
    chemists = User.query.filter(
        User.is_active_user.is_(True),
    ).join(user_roles).filter(
        user_roles.c.role == Role.CHEMIST,
    )
    if current_user.branches:
        chemists = chemists.join(user_branches).filter(
            user_branches.c.branch.in_(current_user.branches)
        )
    chemists = chemists.order_by(User.last_name).all()
    form.chemist_ids.choices = [(c.id, c.full_name) for c in chemists]

    if form.validate_on_submit():
        # Determine test names: from multi-select dropdown or free-text
        if has_predefined_tests and form.test_names.data:
            selected_test_names = form.test_names.data
        elif form.test_name.data:
            selected_test_names = [form.test_name.data]
        else:
            flash('At least one test name is required.', 'danger')
            return render_template(
                'samples/assign.html', form=form, sample=sample,
                today=jamaica_now().date().isoformat(),
                has_predefined_tests=has_predefined_tests,
                has_predefined_refs=has_predefined_refs,
            )

        # Determine test reference(s) – multi-select supported
        if has_predefined_refs and form.test_reference_select.data:
            # Filter out empty values from multi-select
            selected_refs = [r for r in form.test_reference_select.data if r]
            # Use semicolon delimiter for robust parsing when multiple refs selected
            test_ref = '; '.join(selected_refs) if selected_refs else form.test_reference.data
        else:
            test_ref = form.test_reference.data

        assignment_count = 0
        skipped_count = 0
        for chemist_id in form.chemist_ids.data:
            for test_name in selected_test_names:
                # Prevent duplicate assignments for the same
                # (sample, chemist, test_name) combination.
                existing = SampleAssignment.query.filter_by(
                    sample_id=sample.id,
                    chemist_id=chemist_id,
                    test_name=test_name,
                ).filter(
                    SampleAssignment.status.notin_([
                        AssignmentStatus.REJECTED,
                    ])
                ).first()
                if existing:
                    skipped_count += 1
                    continue

                assignment = SampleAssignment(
                    sample_id=sample.id,
                    chemist_id=chemist_id,
                    assigned_by=current_user.id,
                    test_name=test_name,
                    test_reference=test_ref,
                    expected_completion=form.expected_completion.data,
                    comments=form.comments.data or None,
                    quantity_volume=form.quantity_volume.data or None,
                    oos_investigation=bool(form.oos_investigation.data),
                )
                db.session.add(assignment)
                db.session.flush()
                notify_sample_assigned(assignment)
                assignment_count += 1

        if assignment_count == 0:
            db.session.rollback()
            flash('All selected test/chemist combinations are already assigned.', 'warning')
            return redirect(url_for('samples.detail', sample_id=sample.id))

        sample.status = SampleStatus.ASSIGNED
        # Collect assigned chemist names for the history detail
        assigned_chemist_names = [
            c.full_name for c in chemists
            if c.id in form.chemist_ids.data
        ]
        test_list = ', '.join(selected_test_names)
        chemist_list = ', '.join(assigned_chemist_names) if assigned_chemist_names else 'N/A'
        _add_history(
            sample, 'Sample Assigned',
            (f'{current_user.full_name} assigned {assignment_count} test(s) '
             f'({test_list}) to {chemist_list}'),
            action_type='Assignment',
            object_affected='Sample Assignment',
            change_description=(
                f'Tests: {test_list}; '
                f'Assigned to: {chemist_list}; '
                f'Assigned by: {current_user.full_name}'),
        )
        db.session.add(AuditLog(
            action='SAMPLE_ASSIGNED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'tests_assigned': selected_test_names,
                'assigned_to': assigned_chemist_names,
                'assignment_count': assignment_count,
                'assigned_by': current_user.full_name,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()
        if skipped_count > 0:
            flash(f'Sample assigned successfully. {skipped_count} duplicate assignment(s) skipped.', 'success')
        else:
            flash('Sample assigned successfully.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    return render_template('samples/assign.html', form=form, sample=sample,
                           today=jamaica_now().date().isoformat(),
                           has_predefined_tests=has_predefined_tests,
                           has_predefined_refs=has_predefined_refs)


# ---------------------------------------------------------------------------
# Assignment detail (for chemist)
# ---------------------------------------------------------------------------

@samples_bp.route('/assignment/<int:assignment_id>')
@login_required
def assignment_detail(assignment_id):
    assignment = db.get_or_404(SampleAssignment, assignment_id)
    # Access: the assigned chemist, branch heads, officer who uploaded, admin
    if not _can_view_assignment(assignment):
        abort(403)
    review_page = request.args.get('review_page', 1, type=int)
    review_pagination = ReviewHistory.query.filter_by(
        assignment_id=assignment.id
    ).order_by(ReviewHistory.reviewed_at.desc()).paginate(
        page=review_page, per_page=10, error_out=False
    )
    return render_template(
        'samples/assignment_detail.html',
        assignment=assignment,
        review_histories=review_pagination.items,
        review_pagination=review_pagination,
    )


def _can_view_assignment(assignment):
    if current_user.has_role(Role.ADMIN):
        return True
    if current_user.id == assignment.chemist_id:
        return True
    if current_user.id == assignment.sample.uploaded_by:
        return True
    if current_user.has_role(Role.OFFICER):
        return True
    if current_user.is_branch_head():
        if not current_user.branches or assignment.sample.sample_type in current_user.branches:
            return True
    return False


# ---------------------------------------------------------------------------
# Remove assignment (Admin, HOD, or the user who assigned)
# ---------------------------------------------------------------------------

@samples_bp.route('/assignment/<int:assignment_id>/remove', methods=['POST'])
@login_required
def remove_assignment(assignment_id):
    assignment = db.get_or_404(SampleAssignment, assignment_id)
    sample = assignment.sample

    # Admin, HOD, Senior Chemist, the user who made the assignment, or a user
    # explicitly granted ASSIGN_SAMPLE can remove it (explicit permission
    # grants override role restrictions).
    can_remove = (
        current_user.has_permission(Permission.ASSIGN_SAMPLE)
        or current_user.has_any_role(Role.ADMIN, Role.HOD, Role.SENIOR_CHEMIST)
        or current_user.id == assignment.assigned_by
    )
    if not can_remove:
        flash('You do not have permission to remove this assignment.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    chemist_name = assignment.chemist.full_name
    test_name = assignment.test_name
    chemist_id = assignment.chemist_id
    sample_ref = sample.lab_number
    _add_history(
        sample, 'Assignment Removed',
        (f'{current_user.full_name} removed assignment of test '
         f'"{test_name}" from {chemist_name}.'),
        action_type='Assignment Removed',
        object_affected='Sample Assignment',
        change_description=(
            f'Test "{test_name}" unassigned from {chemist_name} '
            f'by {current_user.full_name}'),
    )
    db.session.add(AuditLog(
        action='ASSIGNMENT_REMOVED',
        entity_type='SampleAssignment',
        entity_id=assignment_id,
        entity_label=f'{sample_ref} – {test_name}',
        details=json.dumps({
            'lab_number': sample_ref,
            'test_name': test_name,
            'removed_from': chemist_name,
            'removed_by': current_user.full_name,
        }),
        performed_by=current_user.id,
    ))

    db.session.delete(assignment)

    # Update sample status based on remaining assignments
    remaining = sample.assignments.filter(
        SampleAssignment.id != assignment_id
    ).all()
    if not remaining:
        sample.status = SampleStatus.REGISTERED
    else:
        _update_sample_status(sample)

    db.session.commit()

    # Notify the removed assignee
    notify_assignment_removed(
        chemist_id, sample_ref, test_name, current_user.full_name, sample.id
    )
    db.session.commit()

    flash(f'Assignment of "{test_name}" to {chemist_name} has been removed.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id))


# ---------------------------------------------------------------------------
# Return assignment to analyst (Senior Chemist / HOD at any stage)
# ---------------------------------------------------------------------------

@samples_bp.route('/assignment/<int:assignment_id>/return-to-analyst', methods=['POST'])
@login_required
def return_to_analyst(assignment_id):
    assignment = db.get_or_404(SampleAssignment, assignment_id)
    sample = assignment.sample

    # Explicit TECHNICAL_REVIEW permission overrides role restrictions.
    if not (current_user.has_permission(Permission.TECHNICAL_REVIEW)
            or current_user.has_any_role(Role.SENIOR_CHEMIST, Role.HOD, Role.ADMIN)):
        flash('Only Senior Chemists, HOD, or Admins can return assignments to the analyst.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    # Cannot return an assignment that is already with the analyst
    non_returnable = {AssignmentStatus.ASSIGNED, AssignmentStatus.RETURNED}
    if assignment.status in non_returnable:
        flash('Assignment is already assigned to the analyst.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    old_status = assignment.status.value
    return_comment = request.form.get('return_comment', '').strip() or None

    assignment.status = AssignmentStatus.RETURNED
    assignment.return_stage = 'technical'
    assignment.date_completed = None
    assignment.return_to_analyst_comments = return_comment

    chemist_name = assignment.chemist.full_name if assignment.chemist else 'Unknown'
    history_comment = f' Comments: {return_comment}' if return_comment else ''
    _add_history(
        sample,
        'Returned to Analyst',
        (f'{current_user.full_name} returned assignment for test '
         f'"{assignment.test_name}" to analyst {chemist_name} '
         f'(was: {old_status}).{history_comment}'),
        action_type='Return to Analyst',
        object_affected='Sample Assignment',
        change_description=(
            f'Test "{assignment.test_name}" returned to {chemist_name} '
            f'by {current_user.full_name} (from {old_status}){history_comment}'),
    )

    _update_sample_status(sample)
    db.session.add(AuditLog(
        action='RETURNED_TO_ANALYST',
        entity_type='SampleAssignment',
        entity_id=assignment.id,
        entity_label=f'{sample.lab_number} – {assignment.test_name}',
        details=json.dumps({
            'lab_number': sample.lab_number,
            'test_name': assignment.test_name,
            'analyst': chemist_name,
            'previous_status': old_status,
            'returned_by': current_user.full_name,
            'comments': return_comment or None,
        }),
        performed_by=current_user.id,
    ))
    db.session.commit()

    # Notify the analyst
    from app.notifications import create_notification
    notif_message = (
        f'Your assignment for test "{assignment.test_name}" on sample '
        f'"{sample.sample_name}" (Lab# {sample.lab_number}) has been '
        f'returned to you for correction by {current_user.full_name}.'
    )
    if return_comment:
        notif_message += f'\n\nComments: {return_comment}'
    create_notification(
        assignment.chemist_id,
        f'Assignment Returned: {sample.lab_number}',
        notif_message,
        f'/samples/assignment/{assignment.id}',
    )
    db.session.commit()

    flash(f'Assignment returned to analyst {chemist_name}.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id))


# ---------------------------------------------------------------------------
# Edit assignment (Senior Chemist, HOD, Admin)
# ---------------------------------------------------------------------------

@samples_bp.route('/assignment/<int:assignment_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_assignment(assignment_id):
    assignment = db.get_or_404(SampleAssignment, assignment_id)
    sample = assignment.sample

    # Senior Chemist, HOD, Admin, or an explicit ASSIGN_SAMPLE permission
    # grant can edit assignments (explicit permission overrides roles).
    if not (current_user.has_permission(Permission.ASSIGN_SAMPLE)
            or current_user.has_any_role(Role.SENIOR_CHEMIST, Role.HOD, Role.ADMIN)):
        flash('Only Senior Chemists, HOD, or Admins can edit assignments.', 'danger')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    branch_key = sample.sample_type.name
    # Try dynamic dropdown config first, fall back to hardcoded lists
    dynamic_tests = DropdownConfig.choices_for('test_name', branch=sample.sample_type.value)
    predefined_tests = dynamic_tests if dynamic_tests else BRANCH_TEST_NAMES.get(branch_key)
    predefined_refs = BRANCH_TEST_REFERENCES.get(branch_key)

    # Populate chemist choices
    chemists = User.query.filter(
        User.is_active_user.is_(True),
    ).join(user_roles).filter(
        user_roles.c.role == Role.CHEMIST,
    )
    if current_user.branches:
        chemists = chemists.join(user_branches).filter(
            user_branches.c.branch.in_(current_user.branches)
        )
    chemists = chemists.order_by(User.last_name).all()

    if request.method == 'POST':
        new_chemist_id = request.form.get('chemist_id', type=int)
        new_test_name = request.form.get('test_name', '').strip()
        new_test_reference = request.form.get('test_reference', '').strip()
        new_expected = request.form.get('expected_completion', '').strip()
        new_comments = request.form.get('comments', '').strip()

        changes = []
        if new_chemist_id and new_chemist_id != assignment.chemist_id:
            old_chemist = assignment.chemist.full_name if assignment.chemist else 'Unknown'
            new_chemist = db.session.get(User, new_chemist_id)
            if not new_chemist:
                flash('Selected chemist not found.', 'danger')
                return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))
            assignment.chemist_id = new_chemist_id
            changes.append(f'Reassigned: {old_chemist} → {new_chemist.full_name}')
            # Notify the new chemist
            notify_sample_assigned(assignment)
        if new_test_name and new_test_name != assignment.test_name:
            changes.append(f'Test: {assignment.test_name} → {new_test_name}')
            assignment.test_name = new_test_name
        if new_test_reference != (assignment.test_reference or ''):
            assignment.test_reference = new_test_reference or None
            changes.append(f'Reference updated')
        if new_expected:
            from datetime import datetime as dt
            assignment.expected_completion = dt.strptime(new_expected, '%Y-%m-%d').date()
        if new_comments:
            assignment.comments = new_comments

        if changes:
            chemist_name = assignment.chemist.full_name if assignment.chemist else 'Unknown'
            _add_history(
                sample, 'Assignment Edited',
                (f'{current_user.full_name} edited assignment for '
                 f'{chemist_name}: {"; ".join(changes)}'),
                action_type='Assignment Edit',
                object_affected='Sample Assignment',
                change_description='; '.join(changes) + f' (by {current_user.full_name})',
            )
            db.session.add(AuditLog(
                action='ASSIGNMENT_UPDATED',
                entity_type='SampleAssignment',
                entity_id=assignment.id,
                entity_label=f'{sample.lab_number} – {assignment.test_name}',
                details=json.dumps({
                    'lab_number': sample.lab_number,
                    'test_name': assignment.test_name,
                    'changes': changes,
                    'updated_by': current_user.full_name,
                }),
                performed_by=current_user.id,
            ))
            db.session.commit()
            flash('Assignment updated.', 'success')
        else:
            flash('No changes made.', 'info')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    return render_template(
        'samples/edit_assignment.html',
        assignment=assignment,
        sample=sample,
        chemists=chemists,
        predefined_tests=predefined_tests or [],
        predefined_refs=predefined_refs or [],
        today=jamaica_now().date().isoformat(),
    )


# ---------------------------------------------------------------------------
# Supporting documents upload
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/upload-supporting-doc', methods=['GET', 'POST'])
@login_required
def upload_supporting_document(sample_id):
    sample = db.get_or_404(Sample, sample_id)

    # Only users with the ADD_SUPPORTING_DOCUMENT permission, Officers, Admin,
    # HOD, GC Assistants, or the sample uploader may add documents
    if not (
        current_user.has_permission(Permission.ADD_SUPPORTING_DOCUMENT)
        or current_user.has_any_role(Role.OFFICER, Role.ADMIN, Role.HOD, Role.GOVT_CHEMIST_ASSISTANT)
        or current_user.id == sample.uploaded_by
    ):
        flash('Access denied.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = SupportingDocumentForm()

    if form.validate_on_submit():
        stored, original = _save_file(form.file.data)
        doc = SupportingDocument(
            sample_id=sample.id,
            file_path=stored,
            original_name=original,
            description=form.description.data or None,
            uploaded_by=current_user.id,
        )
        db.session.add(doc)
        _add_history(sample, 'Supporting Document Uploaded',
                     f'{current_user.full_name} uploaded "{original}"',
                     action_type='Document Upload',
                     object_affected='Supporting Document',
                     change_description=f'File: {original} (uploaded by {current_user.full_name})')
        db.session.add(AuditLog(
            action='DOCUMENT_UPLOADED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'file_name': original,
                'description': form.description.data or None,
                'uploaded_by': current_user.full_name,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()
        flash('Supporting document uploaded.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    return render_template(
        'samples/upload_supporting_doc.html', form=form, sample=sample
    )


@samples_bp.route('/<int:sample_id>/delete-supporting-doc/<int:doc_id>',
                  methods=['POST'])
@login_required
def delete_supporting_document(sample_id, doc_id):
    """Delete a supporting document. Admin / SuperAdmin only."""
    sample = db.get_or_404(Sample, sample_id)
    doc = db.get_or_404(SupportingDocument, doc_id)

    if doc.sample_id != sample.id:
        abort(404)

    # Backend authorisation: Admin / SuperAdmin or explicit permission only.
    if not (
        current_user.has_any_role(Role.ADMIN, Role.SUPER_ADMIN)
        or current_user.has_permission(Permission.DELETE_SUPPORTING_DOCUMENT)
    ):
        flash('You do not have permission to delete supporting documents.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    original_name = doc.original_name
    db.session.delete(doc)
    _add_history(sample, 'Supporting Document Deleted',
                 f'{current_user.full_name} deleted "{original_name}"',
                 action_type='Document Delete',
                 object_affected='Supporting Document',
                 change_description=f'File: {original_name} (deleted by {current_user.full_name})')
    db.session.add(AuditLog(
        action='DOCUMENT_DELETED',
        entity_type='Sample',
        entity_id=sample.id,
        entity_label=sample.lab_number,
        details=json.dumps({
            'lab_number': sample.lab_number,
            'file_name': original_name,
            'deleted_by': current_user.full_name,
        }),
        performed_by=current_user.id,
        human_description=(
            f'{current_user.full_name} deleted supporting document '
            f'"{original_name}" from Sample #{sample.lab_number}.'
        ),
        ip_address=request.remote_addr,
        success=True,
    ))
    db.session.commit()
    flash(f'Supporting document "{original_name}" deleted.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id))


# ---------------------------------------------------------------------------
# Sample images upload / delete (previewed on the detail page)
# ---------------------------------------------------------------------------

def _can_manage_sample_images(sample):
    """Return True if the current user may add/remove images for a sample."""
    return (
        current_user.has_permission(Permission.ADD_SUPPORTING_DOCUMENT)
        or current_user.has_any_role(
            Role.OFFICER, Role.ADMIN, Role.HOD, Role.DEPUTY,
            Role.SENIOR_CHEMIST, Role.GOVT_CHEMIST_ASSISTANT,
        )
        or current_user.id == sample.uploaded_by
    )


@samples_bp.route('/<int:sample_id>/upload-image', methods=['POST'])
@login_required
def upload_sample_image(sample_id):
    sample = db.get_or_404(Sample, sample_id)

    if not _can_manage_sample_images(sample):
        flash('Access denied.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = SampleImageForm()
    if form.validate_on_submit():
        stored, original = _save_file(form.image.data)
        image = SampleImage(
            sample_id=sample.id,
            file_path=stored,
            original_name=original,
            caption=form.caption.data or None,
            uploaded_by=current_user.id,
        )
        db.session.add(image)
        _add_history(
            sample, 'Sample Image Uploaded',
            f'{current_user.full_name} uploaded image "{original}"',
            action_type='Image Upload',
            object_affected='Sample Image',
            change_description=f'Image: {original} (uploaded by {current_user.full_name})',
        )
        db.session.add(AuditLog(
            action='SAMPLE_IMAGE_UPLOADED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'file_name': original,
                'caption': form.caption.data or None,
                'uploaded_by': current_user.full_name,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()
        flash('Sample image uploaded.', 'success')
    else:
        for errors in form.errors.values():
            for error in errors:
                flash(error, 'danger')

    return redirect(url_for('samples.detail', sample_id=sample.id) + '#sample-images')


@samples_bp.route('/<int:sample_id>/images/<int:image_id>/delete', methods=['POST'])
@login_required
def delete_sample_image(sample_id, image_id):
    sample = db.get_or_404(Sample, sample_id)
    image = db.get_or_404(SampleImage, image_id)

    if image.sample_id != sample.id:
        abort(404)

    if not _can_manage_sample_images(sample):
        flash('Access denied.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    original = image.original_name
    db.session.delete(image)
    _add_history(
        sample, 'Sample Image Removed',
        f'{current_user.full_name} removed image "{original}"',
        action_type='Image Removal',
        object_affected='Sample Image',
        change_description=f'Image: {original} (removed by {current_user.full_name})',
    )
    db.session.add(AuditLog(
        action='SAMPLE_IMAGE_DELETED',
        entity_type='Sample',
        entity_id=sample.id,
        entity_label=sample.lab_number,
        details=json.dumps({
            'lab_number': sample.lab_number,
            'file_name': original,
            'removed_by': current_user.full_name,
        }),
        performed_by=current_user.id,
    ))
    db.session.commit()
    flash('Sample image removed.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id) + '#sample-images')


# ---------------------------------------------------------------------------
# API: pending assignments for the current analyst (used by the test-selection
# modal on the report-submission workflow)
# ---------------------------------------------------------------------------

@samples_bp.route('/api/my-pending-assignments')
@login_required
def api_my_pending_assignments():
    """Return the current user's pending assignments as JSON.

    Response shape::

        {
            "assignments": [
                {
                    "id": 42,
                    "test_name": "Alcohol Content",
                    "test_reference": "AOAC 2016.12",
                    "sample_id": 7,
                    "lab_number": "FA-2026-001",
                    "sample_name": "Rum Sample A",
                    "status": "Assigned"
                },
                ...
            ]
        }
    """
    pending = SampleAssignment.query.filter(
        SampleAssignment.chemist_id == current_user.id,
        SampleAssignment.status.in_([
            AssignmentStatus.ASSIGNED,
            AssignmentStatus.IN_PROGRESS,
            AssignmentStatus.RETURNED,
        ]),
    ).all()
    pending.sort(key=lambda a: (a.sample.lab_number, a.test_name))
    data = [
        {
            'id': a.id,
            'test_name': a.test_name,
            'test_reference': a.test_reference or '',
            'sample_id': a.sample_id,
            'lab_number': a.sample.lab_number,
            'sample_name': a.sample.sample_name,
            'status': a.status.value,
        }
        for a in pending
    ]
    return jsonify({'assignments': data})


# ---------------------------------------------------------------------------
# Submit report (chemist)
# ---------------------------------------------------------------------------

@samples_bp.route('/assignment/<int:assignment_id>/report', methods=['GET', 'POST'])
@login_required
def submit_report(assignment_id):
    assignment = db.get_or_404(SampleAssignment, assignment_id)
    if current_user.id != assignment.chemist_id:
        flash('Only the assigned chemist can submit a report.', 'danger')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    if assignment.status not in (
        AssignmentStatus.ASSIGNED,
        AssignmentStatus.IN_PROGRESS,
        AssignmentStatus.RETURNED,
    ):
        flash('Report cannot be submitted in the current state.', 'warning')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    is_returned = assignment.status == AssignmentStatus.RETURNED

    # Parse optional pre_selected IDs passed from the test-selection modal
    # (comma-separated assignment IDs in the query string, e.g. ?pre_selected=3,7,12)
    pre_selected_ids: set[int] = set()
    pre_selected_raw = request.args.get('pre_selected', '')
    if pre_selected_raw:
        for _x in pre_selected_raw.split(','):
            try:
                pre_selected_ids.add(int(_x.strip()))
            except (ValueError, TypeError):
                pass

    # Fetch all pending assignments across ALL samples for this chemist so
    # they can link one report to tests from different samples in one go.
    # This applies to both fresh submissions and returned-for-correction resubmissions
    # so the analyst/chemist can always choose which tests to include via the modal.
    all_pending = SampleAssignment.query.filter(
        SampleAssignment.chemist_id == current_user.id,
        SampleAssignment.status.in_([
            AssignmentStatus.ASSIGNED,
            AssignmentStatus.IN_PROGRESS,
            AssignmentStatus.RETURNED,
        ]),
    ).all()
    # Sort: current sample first, then by lab number, then test name
    all_pending.sort(key=lambda a: (
        0 if a.sample_id == assignment.sample_id else 1,
        a.sample.lab_number,
        a.test_name,
    ))
    available_assignments = all_pending
    # When pre-selected IDs were passed (from the test-selection modal),
    # use them as the initial sibling selection.
    if pre_selected_ids:
        sibling_assignments = [a for a in available_assignments if a.id in pre_selected_ids]
        # Always keep at least the current assignment
        if not sibling_assignments:
            sibling_assignments = [assignment]
    elif is_returned:
        # For returned assignments without explicit pre-selection,
        # default to just this assignment (the one being corrected).
        sibling_assignments = [assignment]
    else:
        sibling_assignments = list(available_assignments)

    today = jamaica_now().date()
    min_test_date = assignment.sample.date_received

    form = ReportSubmitForm()
    if form.validate_on_submit():
        now = jamaica_now()
        report_text = form.report_text.data
        all_returned = form.all_samples_returned.data or None
        return_qty = form.return_quantity.data or None
        test_date = form.test_date.data
        meets_spec = form.meets_specifications.data or None
        report_comments = form.report_comments.data or None

        # Resolve which assignments were selected via the test-selection form
        report_mode = request.form.get('report_mode', 'combined')
        if len(available_assignments) > 1:
            selected_ids_raw = request.form.getlist('assignment_ids')
            if selected_ids_raw:
                selected_ids = set()
                for x in selected_ids_raw:
                    try:
                        selected_ids.add(int(x))
                    except (ValueError, TypeError):
                        pass
                sibling_assignments = [a for a in available_assignments if a.id in selected_ids]
            if not sibling_assignments:
                if is_returned:
                    # Safe fallback for returned assignments: only resubmit this one
                    sibling_assignments = [assignment]
                else:
                    flash('Please select at least one test to submit a report for.', 'danger')
                    return render_template(
                        'samples/submit_report.html', form=form, assignment=assignment,
                        available_assignments=available_assignments,
                        sibling_assignments=sibling_assignments,
                        report_mode=report_mode, is_returned=is_returned,
                        today=today.isoformat(), min_test_date=min_test_date.isoformat(),
                    )
        else:
            report_mode = 'combined'

        # Validate test_date bounds for combined mode (single date applies to all selected tests)
        if test_date and report_mode == 'combined':
            if test_date > today:
                flash('Test date cannot be in the future.', 'danger')
                return render_template(
                    'samples/submit_report.html', form=form, assignment=assignment,
                    available_assignments=available_assignments,
                    sibling_assignments=sibling_assignments,
                    report_mode=report_mode, is_returned=is_returned,
                    today=today.isoformat(), min_test_date=min_test_date.isoformat(),
                )
            if test_date < min_test_date:
                flash('Test date cannot be before the date the sample was received.', 'danger')
                return render_template(
                    'samples/submit_report.html', form=form, assignment=assignment,
                    available_assignments=available_assignments,
                    sibling_assignments=sibling_assignments,
                    report_mode=report_mode, is_returned=is_returned,
                    today=today.isoformat(), min_test_date=min_test_date.isoformat(),
                )

        from app.models import DocumentVersion

        if report_mode == 'individual':
            # Validate that every selected assignment has its own file
            missing_files = []
            for a in sibling_assignments:
                f = request.files.get(f'report_file_{a.id}')
                if not f or not f.filename:
                    missing_files.append(a.test_name)
            if missing_files:
                flash(
                    'A report file is required for each selected test: '
                    + ', '.join(missing_files),
                    'danger',
                )
                return render_template(
                    'samples/submit_report.html', form=form, assignment=assignment,
                    available_assignments=available_assignments,
                    sibling_assignments=sibling_assignments,
                    report_mode=report_mode, is_returned=is_returned,
                    today=today.isoformat(), min_test_date=min_test_date.isoformat(),
                )

            # Save per-test files
            per_test_files = {}
            for a in sibling_assignments:
                stored, original = _save_file(request.files[f'report_file_{a.id}'])
                existing_versions = DocumentVersion.query.filter_by(
                    sample_id=a.sample_id, document_type='report',
                    assignment_id=a.id,
                ).count()
                version_num = existing_versions + 1
                upload_label = 'original' if version_num == 1 else 'resubmission'
                resub_type = a.return_stage if upload_label == 'resubmission' else None
                if upload_label == 'resubmission' and not resub_type:
                    resub_type = 'unspecified'
                db.session.add(DocumentVersion(
                    sample_id=a.sample_id,
                    document_type='report',
                    version_number=version_num,
                    file_path=stored,
                    original_name=original,
                    upload_label=upload_label,
                    resubmission_type=resub_type,
                    uploaded_by=current_user.id,
                    assignment_id=a.id,
                ))
                per_test_files[a.id] = (stored, original)
        else:
            # Combined mode: one file shared by all selected assignments
            if not form.report_file.data:
                flash('A report file is required before submitting.', 'danger')
                return render_template(
                    'samples/submit_report.html', form=form, assignment=assignment,
                    available_assignments=available_assignments,
                    sibling_assignments=sibling_assignments,
                    report_mode=report_mode, is_returned=is_returned,
                    today=today.isoformat(), min_test_date=min_test_date.isoformat(),
                )

            stored = original = None
            stored, original = _save_file(form.report_file.data)

            # Record a DocumentVersion entry for every selected assignment so
            # the shared file appears in each assignment's document history.
            for a in sibling_assignments:
                existing_versions = DocumentVersion.query.filter_by(
                    sample_id=a.sample_id, document_type='report',
                    assignment_id=a.id,
                ).count()
                version_num = existing_versions + 1
                upload_label = 'original' if version_num == 1 else 'resubmission'
                resub_type = a.return_stage if upload_label == 'resubmission' else None
                if upload_label == 'resubmission' and not resub_type:
                    resub_type = 'unspecified'
                db.session.add(DocumentVersion(
                    sample_id=a.sample_id,
                    document_type='report',
                    version_number=version_num,
                    file_path=stored,
                    original_name=original,
                    upload_label=upload_label,
                    resubmission_type=resub_type,
                    uploaded_by=current_user.id,
                    assignment_id=a.id,
                ))

        # Check if per-test date/spec fields were submitted (multiple selected assignments)
        has_per_test = len(sibling_assignments) > 1

        # Apply the report to all selected assignments
        date_error = None
        submitted_names = []
        for a in sibling_assignments:
            a.report_text = report_text
            a.report_submitted_at = now
            a.all_samples_returned = all_returned
            a.return_quantity = return_qty
            a.report_comments = report_comments

            if has_per_test:
                # Per-test test_date and meets_specifications from form fields
                per_test_date_str = request.form.get(f'test_date_{a.id}', '')
                per_meets_spec = request.form.get(f'meets_spec_{a.id}') or None
                if per_test_date_str:
                    try:
                        parsed_date = datetime.strptime(per_test_date_str, '%Y-%m-%d').date()
                        if parsed_date > today:
                            date_error = f'Test date for {a.test_name} cannot be in the future.'
                        elif parsed_date < min_test_date:
                            date_error = f'Test date for {a.test_name} cannot be before the date the sample was received.'
                        else:
                            a.test_date = parsed_date
                    except ValueError:
                        a.test_date = None
                else:
                    a.test_date = None
                a.meets_specifications = per_meets_spec
            else:
                a.test_date = test_date
                a.meets_specifications = meets_spec

        if date_error:
            flash(date_error, 'danger')
            return render_template(
                'samples/submit_report.html', form=form, assignment=assignment,
                available_assignments=available_assignments,
                sibling_assignments=sibling_assignments,
                report_mode=report_mode, is_returned=is_returned,
                today=today.isoformat(), min_test_date=min_test_date.isoformat(),
            )

        for a in sibling_assignments:
            if report_mode == 'individual':
                a.report_file, a.report_file_original_name = per_test_files[a.id]
            elif stored:
                a.report_file = stored
                a.report_file_original_name = original

            # Route to correct review stage based on where it was returned from
            if a.return_stage == 'technical':
                a.status = AssignmentStatus.UNDER_TECHNICAL_REVIEW
            else:
                a.status = AssignmentStatus.REPORT_SUBMITTED

            a.return_stage = None
            submitted_names.append(a.test_name)

        # Record history and update status for every unique sample affected
        affected_samples: dict[int, dict] = {}
        for a in sibling_assignments:
            if a.sample_id not in affected_samples:
                affected_samples[a.sample_id] = {'sample': a.sample, 'names': []}
            affected_samples[a.sample_id]['names'].append(a.test_name)

        for _sid, info in affected_samples.items():
            names = info['names']
            _add_history(
                info['sample'], 'Report Submitted',
                (f'{current_user.full_name} submitted report for test(s): '
                 f'{", ".join(names)}'),
                action_type='Report Submission',
                object_affected='Report',
                change_description=(
                    f'Report submitted for: {", ".join(names)} '
                    f'by {current_user.full_name}'),
            )
            _update_sample_status(info['sample'])
            db.session.add(AuditLog(
                action='REPORT_SUBMITTED',
                entity_type='Sample',
                entity_id=info['sample'].id,
                entity_label=info['sample'].lab_number,
                details=json.dumps({
                    'lab_number': info['sample'].lab_number,
                    'tests': names,
                    'submitted_by': current_user.full_name,
                }),
                performed_by=current_user.id,
            ))

        db.session.commit()

        for a in sibling_assignments:
            notify_report_submitted(a)
        db.session.commit()

        test_count = len(submitted_names)
        if test_count > 1:
            flash(f'Report submitted for {test_count} tests successfully.', 'success')
        else:
            flash('Report submitted successfully.', 'success')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    # Pre-fill if resubmitting
    if request.method == 'GET' and assignment.report_text:
        form.report_text.data = assignment.report_text
    if request.method == 'GET' and assignment.test_date:
        form.test_date.data = assignment.test_date
    if request.method == 'GET' and assignment.meets_specifications:
        form.meets_specifications.data = assignment.meets_specifications
    if request.method == 'GET' and assignment.report_comments:
        form.report_comments.data = assignment.report_comments

    return render_template(
        'samples/submit_report.html', form=form, assignment=assignment,
        available_assignments=available_assignments,
        sibling_assignments=sibling_assignments,
        pre_selected_ids=pre_selected_ids,
        report_mode='combined', is_returned=is_returned,
        today=today.isoformat(), min_test_date=min_test_date.isoformat(),
    )


# ---------------------------------------------------------------------------
# Preliminary review (Officer / Senior Chemist Technologist)
# ---------------------------------------------------------------------------

@samples_bp.route('/assignment/<int:assignment_id>/preliminary-review', methods=['GET', 'POST'])
@login_required
def preliminary_review(assignment_id):
    assignment = db.get_or_404(SampleAssignment, assignment_id)

    # Officers, Senior Chemists, Deputy, HOD, Admin, or the sample uploader
    # can do preliminary review.  Explicit PRELIMINARY_REVIEW permission also grants access.
    sample = assignment.sample
    allowed = (
        current_user.has_any_role(
            Role.OFFICER, Role.SENIOR_CHEMIST, Role.DEPUTY, Role.HOD, Role.ADMIN
        )
        or current_user.has_permission(Permission.PRELIMINARY_REVIEW)
        or current_user.id == sample.uploaded_by
    )
    if not allowed:
        flash('You do not have permission to perform preliminary reviews.', 'danger')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    if assignment.status != AssignmentStatus.REPORT_SUBMITTED:
        flash('This report is not awaiting preliminary review.', 'warning')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    # Always fetch ALL sibling assignments awaiting preliminary review so the
    # officer can choose which ones to include (like the submit_report workflow).
    all_pending_assignments = SampleAssignment.query.filter(
        SampleAssignment.sample_id == assignment.sample_id,
        SampleAssignment.status == AssignmentStatus.REPORT_SUBMITTED,
    ).all()

    form = PreliminaryReviewForm()
    if form.validate_on_submit():
        action = form.action.data

        # Resolve which assignments the officer selected
        if len(all_pending_assignments) > 1:
            selected_ids_raw = request.form.getlist('assignment_ids')
            if selected_ids_raw:
                selected_ids = set()
                for x in selected_ids_raw:
                    try:
                        selected_ids.add(int(x))
                    except (ValueError, TypeError):
                        pass
                sibling_assignments = [a for a in all_pending_assignments if a.id in selected_ids]
            else:
                sibling_assignments = []
            if not sibling_assignments:
                # No IDs posted (e.g. direct API call) — fall back to current assignment only
                sibling_assignments = [assignment]
        else:
            sibling_assignments = all_pending_assignments

        # Validation: If any checklist item is "No", only allow return
        if form.has_any_no() and action == 'approved':
            flash('Cannot approve: one or more checklist items are marked "No". '
                  'Please return for correction.', 'danger')
            return render_template(
                'samples/preliminary_review.html', form=form,
                assignment=assignment,
                all_pending_assignments=all_pending_assignments,
                sibling_assignments=sibling_assignments,
            )

        now = jamaica_now()
        comments = form.review_comments.data

        # Save checklist answers
        checklist = {}
        for _, fields in form.CHECKLIST_CATEGORIES:
            for field_name in fields:
                checklist[field_name] = getattr(form, field_name).data
        checklist_json = json.dumps(checklist)

        # Apply the review to all sibling assignments
        # Pre-fetch existing review counts to avoid N+1 queries
        sibling_ids = [a.id for a in sibling_assignments]
        existing_counts = dict(
            db.session.query(
                ReviewHistory.assignment_id,
                db.func.count(ReviewHistory.id)
            ).filter(
                ReviewHistory.assignment_id.in_(sibling_ids),
                ReviewHistory.review_type == 'preliminary'
            ).group_by(ReviewHistory.assignment_id).all()
        )

        return_scope = form.return_scope.data or 'single'
        if action in ('returned', 'not_accepted') and len(sibling_assignments) > 1:
            if return_scope == 'all':
                target_assignments = sibling_assignments
            else:
                target_assignments = [assignment]
        else:
            target_assignments = sibling_assignments

        reviewed_names = []
        for a in target_assignments:
            # Log the review in ReviewHistory BEFORE overwriting fields
            prev_count = existing_counts.get(a.id, 0)
            db.session.add(ReviewHistory(
                sample_id=sample.id,
                assignment_id=a.id,
                review_type='preliminary',
                review_number=prev_count + 1,
                action=action,
                reviewer_id=current_user.id,
                reviewed_at=now,
                comments=comments,
                checklist_data=checklist_json,
            ))

            a.preliminary_review_comments = comments
            a.preliminary_reviewed_by = current_user.id
            a.preliminary_reviewed_at = now
            a.preliminary_review_checklist = checklist_json

            if action == 'approved':
                a.status = AssignmentStatus.UNDER_TECHNICAL_REVIEW
            elif action == 'not_accepted':
                a.status = AssignmentStatus.REJECTED
                a.return_stage = 'preliminary'
                a.date_completed = None
            else:  # returned
                a.status = AssignmentStatus.RETURNED
                a.return_stage = 'preliminary'
                a.date_completed = None

            reviewed_names.append(a.test_name)

        test_list = ', '.join(reviewed_names)
        if action == 'approved':
            _add_history(
                sample, 'Preliminary Review Approved',
                (f'{current_user.full_name} approved preliminary review for '
                 f'test(s): {test_list}. '
                 f'Forwarded to Senior Chemist for technical review.'),
                action_type='Preliminary Review',
                object_affected='Report',
                change_description=(
                    f'Tests: {test_list} — approved by {current_user.full_name}, '
                    f'forwarded to Senior Chemist Review'),
            )
        elif action == 'not_accepted':
            _add_history(
                sample, 'Preliminary Review Not Accepted',
                (f'{current_user.full_name} rejected report for test(s): '
                 f'{test_list} — not accepted. '
                 f'Comments: {comments or "N/A"}'),
                action_type='Preliminary Review',
                object_affected='Report',
                change_description=(
                    f'Tests: {test_list} — not accepted (rejected) '
                    f'by {current_user.full_name}. Comments: {comments or "N/A"}'),
            )
        else:
            _add_history(
                sample, 'Preliminary Review Returned',
                (f'{current_user.full_name} returned report for test(s): '
                 f'{test_list} for correction. '
                 f'Comments: {comments or "N/A"}'),
                action_type='Preliminary Review',
                object_affected='Report',
                change_description=(
                    f'Tests: {test_list} — returned for correction '
                    f'by {current_user.full_name}. Comments: {comments or "N/A"}'),
            )

        _update_sample_status(sample)
        if action == 'approved':
            audit_action = 'PRELIMINARY_REVIEW_APPROVED'
        elif action == 'not_accepted':
            audit_action = 'PRELIMINARY_REVIEW_NOT_ACCEPTED'
        else:
            audit_action = 'PRELIMINARY_REVIEW_RETURNED'
        db.session.add(AuditLog(
            action=audit_action,
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'tests': reviewed_names,
                'outcome': action,
                'reviewed_by': current_user.full_name,
                'comments': comments or None,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        for a in target_assignments:
            notify_preliminary_review_completed(a, action)
        db.session.commit()

        action_text = (
            'approved and forwarded' if action == 'approved'
            else 'not accepted (rejected)' if action == 'not_accepted'
            else 'returned for correction'
        )
        flash_level = 'success' if action == 'approved' else 'danger' if action == 'not_accepted' else 'warning'
        test_count = len(reviewed_names)
        if test_count > 1:
            flash(f'{test_count} reports have been {action_text}.', flash_level)
        else:
            flash(f'Report has been {action_text}.', flash_level)
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    return render_template(
        'samples/preliminary_review.html', form=form,
        assignment=assignment, sibling_assignments=all_pending_assignments,
        all_pending_assignments=all_pending_assignments,
    )


# ---------------------------------------------------------------------------
# My Preliminary Reviews (all preliminary reviews completed by the current
# user, across every analyst, including reports that were returned and any
# subsequent preliminary reviews conducted after resubmission).
#
# Visibility is permission-based:
#   - VIEW_ALL_PRELIMINARY_REVIEWS  -> every reviewer's preliminary reviews
#   - VIEW_TEAM_PRELIMINARY_REVIEWS -> reviews performed by reviewers who
#                                       share at least one branch/section
#                                       with the current user (their "team")
#   - otherwise (VIEW_OWN_PRELIMINARY_REVIEWS, the default)
#                                   -> only the current user's own reviews
# ---------------------------------------------------------------------------

def _preliminary_review_scope(user):
    """Resolve the reviewer-visibility scope for the given user.

    Returns a tuple ``(scope, reviewer_ids)`` where ``scope`` is one of
    ``'all'``, ``'team'`` or ``'own'`` and ``reviewer_ids`` is ``None`` when
    scope is ``'all'`` (no reviewer restriction applied) or a list of user
    ids the caller is permitted to see otherwise.
    """
    if user.has_permission(Permission.VIEW_ALL_PRELIMINARY_REVIEWS):
        return 'all', None

    if user.has_permission(Permission.VIEW_TEAM_PRELIMINARY_REVIEWS):
        if user.branches:
            team_ids = {
                row[0] for row in db.session.query(user_branches.c.user_id)
                .filter(user_branches.c.branch.in_(user.branches))
                .distinct().all()
            }
        else:
            team_ids = set()
        team_ids.add(user.id)
        return 'team', sorted(team_ids)

    return 'own', [user.id]


@samples_bp.route('/preliminary-reviews/mine')
@login_required
def my_preliminary_reviews():
    allowed = (
        current_user.has_any_role(
            Role.OFFICER, Role.SENIOR_CHEMIST, Role.DEPUTY, Role.HOD, Role.ADMIN
        )
        or current_user.has_permission(Permission.PRELIMINARY_REVIEW)
        or current_user.has_permission(Permission.VIEW_TEAM_PRELIMINARY_REVIEWS)
        or current_user.has_permission(Permission.VIEW_ALL_PRELIMINARY_REVIEWS)
    )
    if not allowed:
        flash('You do not have permission to view preliminary review history.', 'danger')
        return redirect(url_for('main.dashboard'))

    scope, scope_reviewer_ids = _preliminary_review_scope(current_user)

    analyst_id = request.args.get('analyst_id', type=int, default=0)
    action_filter = request.args.get('action', '').strip()
    search = request.args.get('search', '').strip()
    assigned_to = request.args.get('assigned_to', type=int, default=0)
    branch_filter = request.args.get('branch', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    page = request.args.get('page', 1, type=int)

    # --- Server-side enforcement -------------------------------------------------
    # Never trust the "assigned_to" (reviewer) query param at face value: a
    # user restricted to 'own' or 'team' scope must not be able to view another
    # reviewer's history simply by editing the URL/query string.
    if scope_reviewer_ids is not None and assigned_to and assigned_to not in scope_reviewer_ids:
        flash('You do not have permission to view that reviewer\'s reviews.', 'danger')
        assigned_to = 0
    if scope == 'own':
        # 'own' scope users have no reviewer selector at all — always self.
        assigned_to = current_user.id

    valid_branch_values = {b.name for b in Branch}
    if branch_filter and branch_filter not in valid_branch_values:
        branch_filter = ''

    def _parse_date(value):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None

    date_from_value = _parse_date(date_from)
    date_to_value = _parse_date(date_to)

    base_q = ReviewHistory.query.join(
        SampleAssignment, ReviewHistory.assignment_id == SampleAssignment.id
    ).join(
        Sample, ReviewHistory.sample_id == Sample.id
    ).filter(
        ReviewHistory.review_type == 'preliminary',
    )
    if scope_reviewer_ids is not None:
        base_q = base_q.filter(ReviewHistory.reviewer_id.in_(scope_reviewer_ids))

    # List of analysts (chemists) whose reports fall within the current
    # scope — used to populate the analyst filter and per-analyst summary.
    analyst_ids_q = db.session.query(SampleAssignment.chemist_id).join(
        ReviewHistory, ReviewHistory.assignment_id == SampleAssignment.id
    ).filter(ReviewHistory.review_type == 'preliminary')
    if scope_reviewer_ids is not None:
        analyst_ids_q = analyst_ids_q.filter(ReviewHistory.reviewer_id.in_(scope_reviewer_ids))
    analyst_ids = [row[0] for row in analyst_ids_q.distinct().all()]
    analysts = User.query.filter(User.id.in_(analyst_ids)).order_by(
        User.first_name, User.last_name
    ).all() if analyst_ids else []

    # --- Error handling for missing records --------------------------------
    # If a specific analyst was requested (e.g. via the workload summary
    # "View" action) but that analyst has no reviews within the caller's
    # visibility scope — or does not exist at all — tell the user instead of
    # silently rendering what looks like an unchanged page.
    if analyst_id and analyst_id not in analyst_ids:
        requested_analyst = db.session.get(User, analyst_id)
        if requested_analyst:
            flash(
                f'No preliminary reviews found for {requested_analyst.full_name} '
                'within your visibility scope.', 'warning'
            )
        else:
            flash('The requested analyst record could not be found.', 'warning')
        analyst_id = 0

    # List of reviewers within scope — used to populate the "Assigned To"
    # filter for users with elevated (team/all) visibility.
    reviewers = []
    if scope in ('all', 'team'):
        reviewer_ids_q = db.session.query(ReviewHistory.reviewer_id).filter(
            ReviewHistory.review_type == 'preliminary'
        )
        if scope_reviewer_ids is not None:
            reviewer_ids_q = reviewer_ids_q.filter(ReviewHistory.reviewer_id.in_(scope_reviewer_ids))
        reviewer_ids = [row[0] for row in reviewer_ids_q.distinct().all()]
        reviewers = User.query.filter(User.id.in_(reviewer_ids)).order_by(
            User.first_name, User.last_name
        ).all() if reviewer_ids else []

    # Per-analyst summary counts (independent of the applied filters below,
    # but respecting the resolved visibility scope)
    analyst_summary = []
    if analyst_ids:
        counts_q = db.session.query(
            SampleAssignment.chemist_id,
            ReviewHistory.action,
            db.func.count(ReviewHistory.id),
        ).join(
            SampleAssignment, ReviewHistory.assignment_id == SampleAssignment.id
        ).filter(ReviewHistory.review_type == 'preliminary')
        if scope_reviewer_ids is not None:
            counts_q = counts_q.filter(ReviewHistory.reviewer_id.in_(scope_reviewer_ids))
        counts_rows = counts_q.group_by(SampleAssignment.chemist_id, ReviewHistory.action).all()

        counts_by_analyst = {}
        for chemist_id, action_value, count in counts_rows:
            entry = counts_by_analyst.setdefault(
                chemist_id, {'approved': 0, 'returned': 0, 'total': 0}
            )
            entry[action_value] = entry.get(action_value, 0) + count
            entry['total'] += count

        for analyst in analysts:
            entry = counts_by_analyst.get(analyst.id, {'approved': 0, 'returned': 0, 'total': 0})
            analyst_summary.append({
                'analyst': analyst,
                'approved': entry.get('approved', 0),
                'returned': entry.get('returned', 0),
                'total': entry.get('total', 0),
            })

    q = base_q
    if analyst_id:
        q = q.filter(SampleAssignment.chemist_id == analyst_id)
    if assigned_to:
        q = q.filter(ReviewHistory.reviewer_id == assigned_to)
    if action_filter in ('approved', 'returned'):
        q = q.filter(ReviewHistory.action == action_filter)
    if branch_filter:
        q = q.filter(Sample.sample_type == Branch[branch_filter])
    if date_from_value:
        q = q.filter(db.func.date(ReviewHistory.reviewed_at) >= date_from_value)
    if date_to_value:
        q = q.filter(db.func.date(ReviewHistory.reviewed_at) <= date_to_value)
    if search:
        like = f'%{search}%'
        q = q.filter(db.or_(
            Sample.lab_number.ilike(like),
            SampleAssignment.test_name.ilike(like),
            SampleAssignment.test_reference.ilike(like),
        ))

    q = q.order_by(ReviewHistory.reviewed_at.desc())
    pagination = q.paginate(page=page, per_page=25, error_out=False)

    # --- Audit logging -----------------------------------------------------
    # Record access whenever a user with elevated (team/all) visibility is
    # viewing reviews that are not exclusively their own.
    if scope in ('all', 'team') and (assigned_to == 0 or assigned_to != current_user.id):
        details = {
            'scope': scope,
            'assigned_to': assigned_to or 'all',
            'analyst_id': analyst_id or None,
            'action': action_filter or None,
            'branch': branch_filter or None,
            'date_from': date_from or None,
            'date_to': date_to or None,
            'search': search or None,
        }
        db.session.add(AuditLog(
            action='VIEW_OTHERS_PRELIMINARY_REVIEWS',
            entity_type='ReviewHistory',
            entity_id=assigned_to or None,
            entity_label=(
                'All reviewers' if not assigned_to
                else (db.session.get(User, assigned_to).full_name if db.session.get(User, assigned_to) else str(assigned_to))
            ),
            details=json.dumps(details),
            performed_by=current_user.id,
            performed_at=jamaica_now(),
        ))
        db.session.commit()

    # Resolve the analyst currently being viewed (drill-down from the
    # workload summary "View" action) so the template can show which
    # analyst's records are loaded.
    selected_analyst = next((a for a in analysts if a.id == analyst_id), None)

    return render_template(
        'samples/my_preliminary_reviews.html',
        reviews=pagination.items,
        pagination=pagination,
        analysts=analysts,
        analyst_summary=analyst_summary,
        analyst_id=analyst_id,
        selected_analyst=selected_analyst,
        action_filter=action_filter,
        search=search,
        scope=scope,
        reviewers=reviewers,
        assigned_to=assigned_to,
        branch_filter=branch_filter,
        date_from=date_from,
        date_to=date_to,
        branches=list(Branch),
        can_manage=current_user.has_permission(Permission.MANAGE_PRELIMINARY_REVIEWS),
    )


# ---------------------------------------------------------------------------
# Senior Chemist Review (formerly Technical Review)
# ---------------------------------------------------------------------------

@samples_bp.route('/assignment/<int:assignment_id>/review', methods=['GET', 'POST'])
@login_required
def review_report(assignment_id):
    assignment = db.get_or_404(SampleAssignment, assignment_id)

    if not (
        current_user.is_branch_head()
        or current_user.has_role(Role.ADMIN)
        or current_user.has_permission(Permission.TECHNICAL_REVIEW)
    ):
        flash('Only Senior Chemists / Branch Heads can review reports.', 'danger')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    if assignment.status != AssignmentStatus.UNDER_TECHNICAL_REVIEW:
        flash('This report is not awaiting technical review.', 'warning')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    # Determine whether grouped review mode is enabled
    grouped_mode = Setting.get_bool('technical_review_grouped', default=False)

    # Find sibling assignments for the same sample that are also awaiting
    # technical review (UNDER_TECHNICAL_REVIEW).
    # In grouped mode: include ALL sibling assignments for the sample.
    # In per-test mode: only include this specific assignment.
    sample = assignment.sample
    if grouped_mode:
        sibling_assignments = SampleAssignment.query.filter(
            SampleAssignment.sample_id == sample.id,
            SampleAssignment.status == AssignmentStatus.UNDER_TECHNICAL_REVIEW,
        ).all()
    else:
        sibling_assignments = [assignment]

    form = ReportReviewForm()

    # Populate reassignment choices (only used in per-test mode)
    chemists = User.query.filter(
        User.is_active_user.is_(True),
    ).join(user_roles).filter(
        user_roles.c.role == Role.CHEMIST,
    )
    if current_user.branches:
        chemists = chemists.join(user_branches).filter(
            user_branches.c.branch.in_(current_user.branches)
        )
    chemists = chemists.order_by(User.last_name).all()
    form.reassign_chemist_id.choices = [(0, '-- No Reassignment --')] + [
        (c.id, c.full_name) for c in chemists
    ]

    if form.validate_on_submit():
        action = form.action.data
        now = jamaica_now()
        comments = form.review_comments.data
        out_of_spec_flag = form.out_of_spec.data

        # Pre-fetch existing review counts to avoid N+1 queries
        sibling_ids = [a.id for a in sibling_assignments]
        existing_counts = dict(
            db.session.query(
                ReviewHistory.assignment_id,
                db.func.count(ReviewHistory.id)
            ).filter(
                ReviewHistory.assignment_id.in_(sibling_ids),
                ReviewHistory.review_type == 'technical'
            ).group_by(ReviewHistory.assignment_id).all()
        )

        return_scope = form.return_scope.data or 'single'
        if action == 'returned' and grouped_mode:
            if return_scope == 'all':
                target_assignments = sibling_assignments
            else:
                target_assignments = [assignment]
        else:
            target_assignments = sibling_assignments

        reviewed_names = []
        for a in target_assignments:
            prev_count = existing_counts.get(a.id, 0)
            db.session.add(ReviewHistory(
                sample_id=sample.id,
                assignment_id=a.id,
                review_type='technical',
                review_number=prev_count + 1,
                action=action,
                reviewer_id=current_user.id,
                reviewed_at=now,
                comments=comments,
            ))

            a.out_of_spec = out_of_spec_flag
            a.review_comments = comments
            a.reviewed_by = current_user.id
            a.reviewed_at = now

            if action == 'accepted':
                a.status = AssignmentStatus.ACCEPTED
                a.date_completed = now
            elif action == 'returned':
                a.status = AssignmentStatus.RETURNED
                a.return_stage = 'technical'
                a.date_completed = None

            reviewed_names.append(a.test_name)

        # Handle reassignment (per-test mode only — first/only sibling)
        reassign_msg = ''
        if not grouped_mode:
            single_assignment = sibling_assignments[0]
            chemist_name = single_assignment.chemist.full_name if single_assignment.chemist else 'Unknown'
            reassign_id = form.reassign_chemist_id.data
            if reassign_id and reassign_id != 0 and reassign_id != single_assignment.chemist_id:
                old_chemist_name = chemist_name
                new_chemist = db.session.get(User, reassign_id)
                if new_chemist:
                    single_assignment.chemist_id = reassign_id
                    single_assignment.status = AssignmentStatus.ASSIGNED
                    single_assignment.return_stage = None
                    single_assignment.date_completed = None
                    reassign_msg = (f' Reassigned from {old_chemist_name} '
                                    f'to {new_chemist.full_name}.')
                    notify_sample_assigned(single_assignment)

        out_of_spec_msg = ' [OUT OF SPEC]' if out_of_spec_flag else ''
        test_list = ', '.join(reviewed_names)

        _add_history(
            sample,
            f'Senior Chemist Review – {action.title()}{out_of_spec_msg}',
            (f'{current_user.full_name} {action} report for test(s): '
             f'{test_list}.{out_of_spec_msg}{reassign_msg} '
             f'Comments: {comments or "N/A"}'),
            action_type='Senior Chemist Review',
            object_affected='Report',
            change_description=(
                f'Test(s): {test_list} — {action} '
                f'by {current_user.full_name}'
                f'{out_of_spec_msg}{reassign_msg}'),
        )

        _update_sample_status(sample)
        audit_action = f'TECHNICAL_REVIEW_{action.upper()}'
        db.session.add(AuditLog(
            action=audit_action,
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'tests': reviewed_names,
                'outcome': action,
                'out_of_spec': out_of_spec_flag,
                'reviewed_by': current_user.full_name,
                'reassignment': reassign_msg.strip() or None,
                'comments': comments or None,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        for a in target_assignments:
            notify_report_reviewed(a, action)
        db.session.commit()

        test_count = len(reviewed_names)
        if test_count > 1:
            flash(f'{test_count} reports have been {action}.{reassign_msg}', 'success')
        else:
            flash(f'Report has been {action}.{reassign_msg}', 'success')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    return render_template(
        'samples/review_report.html', form=form, assignment=assignment,
        chemists=chemists,
        sibling_assignments=sibling_assignments,
        grouped_mode=grouped_mode,
    )


# ---------------------------------------------------------------------------
# Submit to Deputy Government Chemist (Senior Chemist)
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/submit-to-deputy', methods=['GET', 'POST'])
@login_required
def submit_to_deputy(sample_id):
    sample = db.get_or_404(Sample, sample_id)

    # Explicit TECHNICAL_REVIEW permission overrides role restrictions.
    if not (current_user.has_permission(Permission.TECHNICAL_REVIEW)
            or current_user.is_branch_head()
            or current_user.has_role(Role.ADMIN)):
        flash('Only Senior Chemists can submit to Deputy.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    if not _can_submit_to_deputy(sample):
        flash('Sample is not ready for Deputy submission.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    is_resubmission = sample.status == SampleStatus.DEPUTY_RETURNED
    is_pharma = sample.sample_type == Branch.PHARMACEUTICAL
    form = SubmitToDeputyForm()

    # Pre-fill the summary report on GET when resubmitting after a deputy return
    if request.method == 'GET' and is_resubmission and sample.summary_report:
        form.summary_report.data = sample.summary_report

    if form.validate_on_submit():
        # For pharmaceutical, require summary report
        if is_pharma and not form.summary_report.data:
            flash('Summary report is required for pharmaceutical samples.', 'danger')
            return render_template(
                'samples/submit_to_deputy.html', form=form, sample=sample,
                is_pharma=is_pharma, is_resubmission=is_resubmission,
            )

        if form.summary_report.data:
            sample.summary_report = form.summary_report.data
            sample.summary_report_by = current_user.id
            sample.summary_report_at = jamaica_now()

        if form.summary_report_file.data:
            stored, original = _save_file(form.summary_report_file.data)
            sample.summary_report_file = stored
            sample.summary_report_file_original_name = original

            # Flush to obtain sample.id for the DocumentVersion FK
            db.session.flush()
            existing_versions = DocumentVersion.query.filter_by(
                sample_id=sample.id, document_type='summary_report'
            ).count()
            version_num = existing_versions + 1
            upload_label = 'original' if version_num == 1 else 'revised'
            db.session.add(DocumentVersion(
                sample_id=sample.id,
                document_type='summary_report',
                version_number=version_num,
                file_path=stored,
                original_name=original,
                upload_label=upload_label,
                uploaded_by=current_user.id,
            ))

        sample.status = SampleStatus.DEPUTY_REVIEW

        if is_resubmission:
            action_label = 'Resubmitted to Deputy'
            detail_parts = [
                f'Resubmitted to Deputy Government Chemist by {current_user.full_name} after corrections.'
            ]
            change_desc = (
                f'Resubmitted to Deputy Government Chemist '
                f'by {current_user.full_name} after corrections.'
            )
            flash_msg = 'Resubmitted to Deputy Government Chemist.'
        else:
            action_label = 'Submitted to Deputy'
            detail_parts = [f'Submitted to Deputy Government Chemist by {current_user.full_name}.']
            change_desc = (
                f'Submitted to Deputy Government Chemist '
                f'by {current_user.full_name}'
            )
            flash_msg = 'Reports submitted to Deputy Government Chemist.'

        if is_pharma:
            detail_parts.append('Summary report included (Pharmaceutical sample).')

        _add_history(sample, action_label, ' '.join(detail_parts),
                     action_type='Deputy Submission',
                     object_affected='Sample',
                     change_description=change_desc)
        audit_action = 'RESUBMITTED_TO_DEPUTY' if is_resubmission else 'SUBMITTED_TO_DEPUTY'
        db.session.add(AuditLog(
            action=audit_action,
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'sample_name': sample.sample_name,
                'sample_type': sample.sample_type.value,
                'is_resubmission': is_resubmission,
                'includes_summary_report': bool(form.summary_report.data),
                'submitted_by': current_user.full_name,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        notify_submitted_to_deputy(sample)
        db.session.commit()

        flash(flash_msg, 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    return render_template(
        'samples/submit_to_deputy.html', form=form, sample=sample,
        is_pharma=is_pharma, is_resubmission=is_resubmission,
    )


# ---------------------------------------------------------------------------
# Deputy Government Chemist review
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/deputy-review', methods=['GET', 'POST'])
@login_required
def deputy_review(sample_id):
    sample = db.get_or_404(Sample, sample_id)

    if not (
        current_user.has_any_role(Role.DEPUTY, Role.HOD, Role.ADMIN)
        or current_user.has_permission(Permission.DEPUTY_REVIEW)
    ):
        flash('Only the Deputy Government Chemist can perform this review.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    if sample.status != SampleStatus.DEPUTY_REVIEW:
        flash('Sample is not awaiting Deputy review.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = DeputyReviewForm()
    if form.validate_on_submit():
        action = form.action.data
        now = jamaica_now()

        # Log the review in ReviewHistory
        prev_count = ReviewHistory.query.filter_by(
            sample_id=sample.id, review_type='deputy'
        ).count()
        db.session.add(ReviewHistory(
            sample_id=sample.id,
            assignment_id=None,
            review_type='deputy',
            review_number=prev_count + 1,
            action=action,
            reviewer_id=current_user.id,
            reviewed_at=now,
            comments=form.review_comments.data,
        ))

        sample.deputy_review_comments = form.review_comments.data
        sample.deputy_reviewed_by = current_user.id
        sample.deputy_reviewed_at = now

        if action == 'approved':
            sample.status = SampleStatus.CERTIFICATE_PREPARATION
            _add_history(
                sample, 'Deputy Review Approved',
                (f'{current_user.full_name} approved the submission. '
                 f'Certificate of Analysis to be prepared.'),
                action_type='Deputy Review',
                object_affected='Sample',
                change_description=(
                    f'Approved by Deputy Government Chemist '
                    f'{current_user.full_name}. '
                    f'Proceeding to Certificate Preparation.'),
            )
        else:  # returned
            sample.status = SampleStatus.DEPUTY_RETURNED
            _add_history(
                sample, 'Deputy Review Returned',
                (f'{current_user.full_name} returned submission to '
                 f'Senior Chemist. Comments: {form.review_comments.data or "N/A"}'),
                action_type='Deputy Review',
                object_affected='Sample',
                change_description=(
                    f'Returned by Deputy Government Chemist '
                    f'{current_user.full_name}. '
                    f'Comments: {form.review_comments.data or "N/A"}'),
            )

        audit_action = f'DEPUTY_REVIEW_{action.upper()}'
        db.session.add(AuditLog(
            action=audit_action,
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'sample_name': sample.sample_name,
                'outcome': action,
                'reviewed_by': current_user.full_name,
                'comments': form.review_comments.data or None,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        notify_deputy_review_completed(sample, action)
        db.session.commit()

        action_text = {
            'approved': 'accepted and forwarded for certificate preparation',
            'returned': 'returned to Senior Chemist',
        }.get(action, action)
        flash(f'Submission has been {action_text}.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    assignments = sample.assignments.all()
    return render_template(
        'samples/deputy_review.html', form=form, sample=sample,
        assignments=assignments,
    )


# ---------------------------------------------------------------------------
# Resubmit to Deputy (Senior Chemist, after deputy return)
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/resubmit-to-deputy', methods=['POST'])
@login_required
def resubmit_to_deputy(sample_id):
    sample = db.get_or_404(Sample, sample_id)

    if not current_user.is_branch_head() and not current_user.has_role(Role.ADMIN):
        flash('Access denied.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    if sample.status != SampleStatus.DEPUTY_RETURNED:
        flash('Sample is not in a returned-by-Deputy state.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    sample.status = SampleStatus.DEPUTY_REVIEW
    _add_history(
        sample, 'Resubmitted to Deputy',
        (f'{current_user.full_name} resubmitted to Deputy Government Chemist '
         f'after corrections.'),
        action_type='Deputy Resubmission',
        object_affected='Sample',
        change_description=(
            f'Resubmitted by {current_user.full_name} after corrections'),
    )
    db.session.add(AuditLog(
        action='RESUBMITTED_TO_DEPUTY',
        entity_type='Sample',
        entity_id=sample.id,
        entity_label=sample.lab_number,
        details=json.dumps({
            'lab_number': sample.lab_number,
            'sample_name': sample.sample_name,
            'resubmitted_by': current_user.full_name,
        }),
        performed_by=current_user.id,
    ))
    db.session.commit()

    notify_submitted_to_deputy(sample)
    db.session.commit()

    flash('Resubmitted to Deputy Government Chemist.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id))


# ---------------------------------------------------------------------------
# Admin: Unreject Sample (restore Rejected sample to Deputy Review)
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/unreject', methods=['POST'])
@login_required
def unreject_sample(sample_id):
    """Admin removes Rejected status, returning the sample to Deputy Review."""
    sample = db.get_or_404(Sample, sample_id)

    if not current_user.has_role(Role.ADMIN):
        flash('Only Administrators can remove a rejected status.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    if sample.status != SampleStatus.REJECTED:
        flash('Sample is not in a Rejected state.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    reason = request.form.get('unreject_reason', '').strip()
    if not reason or len(reason) < 5:
        flash('Please provide a reason (minimum 5 characters).', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    now = jamaica_now()
    sample.status = SampleStatus.DEPUTY_REVIEW

    _add_history(
        sample,
        'Rejected Status Removed',
        (f'{current_user.full_name} (Admin) removed the rejected status. '
         f'Sample returned to Deputy Review. Reason: {reason}'),
        action_type='Admin Action',
        object_affected='Sample',
        change_description=(
            f'Rejected status removed by Admin {current_user.full_name}. '
            f'Returned to Deputy Review. Reason: {reason}'),
    )

    db.session.add(ReviewHistory(
        sample_id=sample.id,
        review_type='deputy',
        review_number=1,
        action='unreject',
        reviewer_id=current_user.id,
        reviewed_at=now,
        comments=reason,
    ))

    db.session.add(AuditLog(
        action='ADMIN_UNREJECT_SAMPLE',
        entity_type='Sample',
        entity_id=sample.id,
        entity_label=sample.lab_number,
        details=json.dumps({
            'lab_number': sample.lab_number,
            'sample_name': sample.sample_name,
            'previous_status': 'Rejected',
            'new_status': SampleStatus.DEPUTY_REVIEW.value,
            'actioned_by': current_user.full_name,
            'reason': reason,
        }),
        performed_by=current_user.id,
    ))

    db.session.commit()

    flash('Rejected status has been removed. Sample is now in Deputy Review.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id))


# ---------------------------------------------------------------------------
# Prepare Certificate of Analysis (Deputy Government Chemist)
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/prepare-certificate', methods=['GET', 'POST'])
@login_required
def prepare_certificate(sample_id):
    sample = db.get_or_404(Sample, sample_id)

    # Explicit DEPUTY_REVIEW permission overrides role restrictions.
    if not (current_user.has_permission(Permission.DEPUTY_REVIEW)
            or current_user.has_any_role(Role.DEPUTY, Role.HOD, Role.ADMIN)):
        flash('Only the Deputy Government Chemist can prepare certificates.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    if sample.status not in (SampleStatus.CERTIFICATE_PREPARATION, SampleStatus.HOD_RETURNED):
        flash('Sample is not ready for certificate preparation.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = CertificateForm()
    if form.validate_on_submit():
        sample.certificate_text = form.certificate_text.data
        sample.certificate_prepared_by = current_user.id
        sample.certificate_prepared_at = jamaica_now()
        sample.coa_reference = form.coa_reference.data or sample.coa_reference

        if form.accreditation.data == 'accredited':
            sample.is_accredited = True
        elif form.accreditation.data == 'not_accredited':
            sample.is_accredited = False

        if form.certificate_file.data:
            stored, original = _save_file(form.certificate_file.data)
            sample.certificate_file = stored
            sample.certificate_file_original_name = original

            # Create a document version entry for the COA
            from app.models import DocumentVersion
            existing_versions = DocumentVersion.query.filter_by(
                sample_id=sample.id, document_type='certificate'
            ).count()
            version_num = existing_versions + 1
            upload_label = 'original' if version_num == 1 else 'revised'
            db.session.add(DocumentVersion(
                sample_id=sample.id,
                document_type='certificate',
                version_number=version_num,
                file_path=stored,
                original_name=original,
                upload_label=upload_label,
                uploaded_by=current_user.id,
            ))

        sample.status = SampleStatus.HOD_REVIEW

        coa_ref = form.coa_reference.data
        _add_history(
            sample, 'Certificate Prepared',
            (f'Certificate of Analysis prepared by {current_user.full_name}. '
             f'Submitted to Government Chemist for review and signing.'
             f'{" COA Ref: " + coa_ref if coa_ref else ""}'),
            action_type='Certificate Preparation',
            object_affected='Certificate of Analysis',
            change_description=(
                f'COA prepared by {current_user.full_name}, '
                f'submitted to Government Chemist for signing'),
        )
        db.session.add(AuditLog(
            action='CERTIFICATE_PREPARED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'sample_name': sample.sample_name,
                'coa_reference': coa_ref or None,
                'prepared_by': current_user.full_name,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        notify_certificate_prepared(sample)
        db.session.commit()

        flash('Certificate of Analysis submitted for Government Chemist review.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    # Pre-fill if resubmitting after HOD return
    if request.method == 'GET' and sample.certificate_text:
        form.certificate_text.data = sample.certificate_text
    if request.method == 'GET' and sample.coa_reference:
        form.coa_reference.data = sample.coa_reference
    if request.method == 'GET' and sample.is_accredited is not None:
        form.accreditation.data = (
            'accredited' if sample.is_accredited else 'not_accredited'
        )

    return render_template(
        'samples/prepare_certificate.html', form=form, sample=sample,
    )


# ---------------------------------------------------------------------------
# HOD (Government Chemist) review & sign certificate
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/hod-review', methods=['GET', 'POST'])
@login_required
def hod_review(sample_id):
    sample = db.get_or_404(Sample, sample_id)

    if not (
        current_user.has_any_role(Role.HOD, Role.ADMIN)
        or current_user.has_permission(Permission.HOD_REVIEW)
    ):
        flash('Only the Government Chemist can review and sign certificates.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    if sample.status != SampleStatus.HOD_REVIEW:
        flash('Sample is not awaiting Government Chemist review.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = HODReviewForm()
    if form.validate_on_submit():
        action = form.action.data
        now = jamaica_now()

        # Log the review in ReviewHistory
        prev_count = ReviewHistory.query.filter_by(
            sample_id=sample.id, review_type='hod'
        ).count()
        db.session.add(ReviewHistory(
            sample_id=sample.id,
            assignment_id=None,
            review_type='hod',
            review_number=prev_count + 1,
            action=action,
            reviewer_id=current_user.id,
            reviewed_at=now,
            comments=form.review_comments.data,
        ))

        sample.hod_review_comments = form.review_comments.data
        sample.hod_reviewed_by = current_user.id
        sample.hod_reviewed_at = now

        if action == 'sign':
            sample.certified_at = now
            sample.certified_by = current_user.id
            sample.status = SampleStatus.CERTIFIED
            _add_history(
                sample, 'Certificate Signed',
                (f'Certificate of Analysis signed by '
                 f'Government Chemist {current_user.full_name}. '
                 f'Sample analysis process completed.'),
                action_type='Certificate Signed',
                object_affected='Certificate of Analysis',
                change_description=(
                    f'Signed by Government Chemist {current_user.full_name}. '
                    f'Process completed.'),
            )
        else:  # returned
            sample.status = SampleStatus.HOD_RETURNED
            _add_history(
                sample, 'Certificate Returned by HOD',
                (f'Government Chemist {current_user.full_name} returned '
                 f'certificate for correction. '
                 f'Comments: {form.review_comments.data or "N/A"}'),
                action_type='HOD Review',
                object_affected='Certificate of Analysis',
                change_description=(
                    f'Returned by Government Chemist {current_user.full_name}. '
                    f'Comments: {form.review_comments.data or "N/A"}'),
            )

        audit_action = 'CERTIFICATE_SIGNED' if action == 'sign' else 'HOD_REVIEW_RETURNED'
        db.session.add(AuditLog(
            action=audit_action,
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'sample_name': sample.sample_name,
                'outcome': action,
                'reviewed_by': current_user.full_name,
                'comments': form.review_comments.data or None,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        notify_certificate_signed(sample, action)
        db.session.commit()

        if action == 'sign':
            flash('Certificate of Analysis signed. Process completed.', 'success')
        else:
            flash('Certificate returned to Deputy for correction.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    return render_template(
        'samples/hod_review.html', form=form, sample=sample,
    )


# ---------------------------------------------------------------------------
# Back-date registration date request
# ---------------------------------------------------------------------------

@samples_bp.route('/<int:sample_id>/request-backdate', methods=['GET', 'POST'])
@login_required
def request_backdate(sample_id):
    """Request to back-date any date field on a sample or assignment."""
    sample = db.get_or_404(Sample, sample_id)

    # Officers, Senior Chemists, Admin, HOD, Chemists can request
    if not current_user.has_any_role(
        Role.OFFICER, Role.SENIOR_CHEMIST, Role.ADMIN, Role.HOD, Role.DEPUTY, Role.CHEMIST
    ):
        flash('You do not have permission to request a back-date.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = BackDateRequestForm()

    # Populate assignment choices
    assignments = sample.assignments.all()
    form.assignment_id.choices = [(0, '-- Sample-level (no assignment) --')] + [
        (a.id, f'{a.test_name} – {a.chemist.full_name if a.chemist else "Unknown"}')
        for a in assignments
    ]

    if form.validate_on_submit():
        field_name = form.field_name.data
        assignment_id_val = form.assignment_id.data if form.assignment_id.data else None
        if assignment_id_val == 0:
            assignment_id_val = None

        # Determine the original date value
        date_fmt = '%Y-%m-%d'
        # Fields on sample
        sample_date_fields = {
            'date_registered': sample.date_registered,
            'date_received': sample.date_received,
            'expected_report_date': sample.expected_report_date,
            'deputy_reviewed_at': sample.deputy_reviewed_at,
            'certificate_prepared_at': sample.certificate_prepared_at,
            'certified_at': sample.certified_at,
        }
        # Fields on assignment
        assignment_date_fields = {
            'assigned_date', 'expected_completion', 'report_submitted_at',
            'test_date', 'reviewed_at',
        }

        original = ''
        if field_name in sample_date_fields:
            val = sample_date_fields[field_name]
            if val:
                original = val.strftime(date_fmt) if hasattr(val, 'strftime') else str(val)
        elif field_name in assignment_date_fields and assignment_id_val:
            asgn = db.session.get(SampleAssignment, assignment_id_val)
            if asgn:
                val = getattr(asgn, field_name, None)
                if val:
                    original = val.strftime(date_fmt) if hasattr(val, 'strftime') else str(val)
        elif field_name in assignment_date_fields and not assignment_id_val:
            flash('Please select an assignment for assignment-level date fields.', 'danger')
            return render_template(
                'samples/request_backdate.html', form=form, sample=sample,
                assignments=assignments,
            )

        proposed = form.proposed_date.data.strftime(date_fmt)

        # Check for existing pending request on this field
        pending_q = BackDateRequest.query.filter_by(
            sample_id=sample.id,
            field_name=field_name,
            status='pending',
        )
        if assignment_id_val:
            pending_q = pending_q.filter_by(assignment_id=assignment_id_val)
        pending = pending_q.first()
        if pending:
            flash(f'A back-date request for "{field_name.replace("_", " ").title()}" is already pending.', 'warning')
            return redirect(url_for('samples.detail', sample_id=sample.id))

        bdr = BackDateRequest(
            sample_id=sample.id,
            assignment_id=assignment_id_val,
            field_name=field_name,
            original_date=original,
            proposed_date=proposed,
            reason=form.reason.data,
            requested_by=current_user.id,
        )
        db.session.add(bdr)

        field_label = field_name.replace('_', ' ').title()
        _add_history(
            sample, 'Back-Date Requested',
            (f'{current_user.full_name} requested to change {field_label} '
             f'from {original or "N/A"} to {proposed}. Reason: {form.reason.data}'),
            action_type='Back-Date Request',
            object_affected='Sample' if not assignment_id_val else 'Assignment',
            change_description=(
                f'{field_name}: {original or "N/A"} → {proposed} '
                f'(requested by {current_user.full_name})'),
        )
        db.session.add(AuditLog(
            action='BACKDATE_REQUESTED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'field': field_label,
                'original_date': original or None,
                'proposed_date': proposed,
                'reason': form.reason.data,
                'requested_by': current_user.full_name,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        notify_backdate_request_submitted(bdr)
        db.session.commit()

        flash('Back-date request submitted for approval.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    return render_template(
        'samples/request_backdate.html', form=form, sample=sample,
        assignments=assignments,
    )


# ---------------------------------------------------------------------------
# Request deletion of a sample (Senior Chemist, Deputy, Officer, GC Assistant)
# ---------------------------------------------------------------------------

# Roles that may submit a deletion request (HOD and Admin can delete directly)
_DELETE_REQUEST_ROLES = (
    Role.SENIOR_CHEMIST, Role.DEPUTY, Role.OFFICER, Role.GOVT_CHEMIST_ASSISTANT,
)


@samples_bp.route('/<int:sample_id>/request-delete', methods=['GET', 'POST'])
@login_required
def request_sample_delete(sample_id):
    """Let authorised staff submit a deletion request for a sample."""
    sample = db.get_or_404(Sample, sample_id)

    if not current_user.has_any_role(*_DELETE_REQUEST_ROLES):
        flash('You do not have permission to request sample deletion.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    # Block if a pending request already exists
    existing = DeleteRequest.query.filter_by(
        request_type='sample', sample_id=sample.id, status='pending'
    ).first()
    if existing:
        flash('A deletion request for this sample is already pending approval.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    form = DeleteRequestForm()
    if form.validate_on_submit():
        uploader = db.session.get(User, sample.uploaded_by)
        snapshot = json.dumps({
            'lab_number': sample.lab_number,
            'sample_name': sample.sample_name,
            'sample_type': sample.sample_type.value,
            'status': sample.status.value,
            'date_received': sample.date_received.isoformat() if sample.date_received else None,
            'date_registered': sample.date_registered.isoformat() if sample.date_registered else None,
            'uploaded_by': sample.uploaded_by,
            'uploaded_by_name': uploader.full_name if uploader else None,
            'assignment_count': sample.assignments.count(),
        })
        dr = DeleteRequest(
            request_type='sample',
            sample_id=sample.id,
            reason=form.reason.data,
            entity_snapshot=snapshot,
            entity_label=sample.lab_number,
            requested_by=current_user.id,
        )
        db.session.add(dr)

        _add_history(
            sample, 'Deletion Requested',
            (f'{current_user.full_name} requested deletion of this sample. '
             f'Reason: {form.reason.data}'),
            action_type='Delete Request',
            object_affected='Sample',
            change_description=f'Deletion requested by {current_user.full_name}',
        )
        db.session.add(AuditLog(
            action='DELETE_REQUEST_SUBMITTED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'sample_name': sample.sample_name,
                'sample_type': sample.sample_type.value,
                'current_status': sample.status.value,
                'reason': form.reason.data,
                'requested_by': current_user.full_name,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        notify_delete_request_submitted(dr)
        db.session.commit()

        flash('Deletion request submitted for HOD approval.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    return render_template(
        'samples/request_delete.html', form=form, sample=sample, assignment=None,
    )


@samples_bp.route('/assignment/<int:assignment_id>/request-delete', methods=['GET', 'POST'])
@login_required
def request_assignment_delete(assignment_id):
    """Let authorised staff submit a deletion request for a test assignment."""
    assignment = db.get_or_404(SampleAssignment, assignment_id)
    sample = assignment.sample

    if not current_user.has_any_role(*_DELETE_REQUEST_ROLES):
        flash('You do not have permission to request assignment deletion.', 'danger')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    # Block if a pending request already exists
    existing = DeleteRequest.query.filter_by(
        request_type='assignment', assignment_id=assignment.id, status='pending'
    ).first()
    if existing:
        flash('A deletion request for this assignment is already pending approval.', 'warning')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    form = DeleteRequestForm()
    if form.validate_on_submit():
        chemist = db.session.get(User, assignment.chemist_id)
        snapshot = json.dumps({
            'assignment_id': assignment.id,
            'sample_lab_number': sample.lab_number,
            'sample_name': sample.sample_name,
            'test_name': assignment.test_name,
            'test_reference': assignment.test_reference,
            'chemist_id': assignment.chemist_id,
            'chemist_name': chemist.full_name if chemist else None,
            'status': assignment.status.value,
            'assigned_date': assignment.assigned_date.isoformat() if assignment.assigned_date else None,
        })
        label = f'{sample.lab_number} – {assignment.test_name}'
        dr = DeleteRequest(
            request_type='assignment',
            sample_id=sample.id,
            assignment_id=assignment.id,
            reason=form.reason.data,
            entity_snapshot=snapshot,
            entity_label=label,
            requested_by=current_user.id,
        )
        db.session.add(dr)

        _add_history(
            sample, 'Assignment Deletion Requested',
            (f'{current_user.full_name} requested deletion of assignment '
             f'"{assignment.test_name}". Reason: {form.reason.data}'),
            action_type='Delete Request',
            object_affected='Sample Assignment',
            change_description=(
                f'Deletion of assignment "{assignment.test_name}" requested '
                f'by {current_user.full_name}'),
        )
        db.session.add(AuditLog(
            action='DELETE_REQUEST_SUBMITTED',
            entity_type='SampleAssignment',
            entity_id=assignment.id,
            entity_label=label,
            details=json.dumps({
                'lab_number': sample.lab_number,
                'test_name': assignment.test_name,
                'chemist': chemist.full_name if chemist else None,
                'current_status': assignment.status.value,
                'reason': form.reason.data,
                'requested_by': current_user.full_name,
            }),
            performed_by=current_user.id,
        ))
        db.session.commit()

        notify_delete_request_submitted(dr)
        db.session.commit()

        flash('Deletion request submitted for HOD approval.', 'success')
        return redirect(url_for('samples.assignment_detail', assignment_id=assignment.id))

    return render_template(
        'samples/request_delete.html', form=form, sample=sample, assignment=assignment,
    )


def _update_sample_status(sample):
    """Derive the overall sample status from assignment statuses.

    This function only manages statuses up to and including ACCEPTED.
    Statuses that are set by the Deputy/HOD workflow are never overridden
    here; those transitions are handled exclusively by their own routes.
    """
    # Statuses set by the Deputy/HOD workflow must not be overridden by
    # assignment-driven logic.  Once a sample reaches any of these states
    # only the relevant Deputy/HOD route may change the status.
    _protected_statuses = {
        SampleStatus.DEPUTY_REVIEW,
        SampleStatus.DEPUTY_RETURNED,
        SampleStatus.CERTIFICATE_PREPARATION,
        SampleStatus.HOD_REVIEW,
        SampleStatus.HOD_RETURNED,
        SampleStatus.CERTIFIED,
    }
    if sample.status in _protected_statuses:
        return

    assignments = sample.assignments.all()
    if not assignments:
        return

    statuses = {a.status for a in assignments}

    if all(s == AssignmentStatus.COMPLETED for s in statuses):
        sample.status = SampleStatus.COMPLETED
    elif all(s in (AssignmentStatus.ACCEPTED, AssignmentStatus.COMPLETED) for s in statuses):
        sample.status = SampleStatus.ACCEPTED
    elif any(s == AssignmentStatus.REJECTED for s in statuses):
        sample.status = SampleStatus.REJECTED
    elif any(s == AssignmentStatus.RETURNED for s in statuses):
        sample.status = SampleStatus.RETURNED
    elif any(s == AssignmentStatus.UNDER_TECHNICAL_REVIEW for s in statuses):
        sample.status = SampleStatus.UNDER_TECHNICAL_REVIEW
    elif any(s == AssignmentStatus.UNDER_PRELIMINARY_REVIEW for s in statuses):
        sample.status = SampleStatus.UNDER_PRELIMINARY_REVIEW
    elif any(s == AssignmentStatus.REPORT_SUBMITTED for s in statuses):
        sample.status = SampleStatus.REPORT_SUBMITTED
    elif any(s == AssignmentStatus.IN_PROGRESS for s in statuses):
        sample.status = SampleStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# File downloads and inline preview
# ---------------------------------------------------------------------------

@samples_bp.route('/download/<path:filename>')
@login_required
def download_file(filename):
    # Prevent path traversal
    safe_name = secure_filename(filename)
    if safe_name != filename:
        abort(404)
    return send_from_directory(
        current_app.config['UPLOAD_FOLDER'], safe_name, as_attachment=True
    )


@samples_bp.route('/view/<path:filename>')
@login_required
def view_file(filename):
    """Serve a file inline (for PDF preview in browser)."""
    safe_name = secure_filename(filename)
    if safe_name != filename:
        abort(404)
    return send_from_directory(
        current_app.config['UPLOAD_FOLDER'], safe_name, as_attachment=False
    )


# ---------------------------------------------------------------------------
# Bulk Delete Samples  (Admin only – always audited)
# ---------------------------------------------------------------------------

@samples_bp.route('/bulk-delete', methods=['POST'])
@login_required
def bulk_delete():
    """Delete one or more samples and all related data.

    Every deletion is recorded in the permanent AuditLog table so that
    the action can never be lost – even after the sample row is gone.
    """
    if not current_user.has_role(Role.ADMIN):
        flash('Access denied.', 'danger')
        return redirect(url_for('samples.sample_list'))

    sample_ids = request.form.getlist('sample_ids', type=int)
    if not sample_ids:
        flash('No samples selected.', 'warning')
        return redirect(url_for('samples.sample_list'))

    samples = Sample.query.filter(Sample.id.in_(sample_ids)).all()
    if not samples:
        flash('No matching samples found.', 'warning')
        return redirect(url_for('samples.sample_list'))

    deleted_labels = []
    now = jamaica_now()

    for sample in samples:
        # Build a snapshot of sample data for the audit log
        uploader = db.session.get(User, sample.uploaded_by)
        snapshot = json.dumps({
            'lab_number': sample.lab_number,
            'sample_name': sample.sample_name,
            'sample_type': sample.sample_type.value,
            'status': sample.status.value,
            'description': sample.description,
            'quantity': sample.quantity,
            'date_received': sample.date_received.isoformat() if sample.date_received else None,
            'date_registered': sample.date_registered.isoformat() if sample.date_registered else None,
            'uploaded_by': sample.uploaded_by,
            'uploaded_by_name': uploader.full_name if uploader else None,
            'assignment_count': sample.assignments.count(),
        })

        # Write permanent audit record
        db.session.add(AuditLog(
            action='SAMPLE_DELETED',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=snapshot,
            performed_by=current_user.id,
            performed_at=now,
        ))

        # Remove uploaded files from disk
        _delete_sample_files(sample)

        # Explicitly delete ReviewHistory records (sample_id is NOT NULL so
        # SQLAlchemy cannot null it out; the cascade on the relationship
        # handles this, but we also do it explicitly for safety).
        ReviewHistory.query.filter_by(sample_id=sample.id).delete(
            synchronize_session=False
        )

        # Delete sample (cascades to assignments, history, supporting docs,
        # document versions, back-date requests via relationship cascades)
        db.session.delete(sample)

        deleted_labels.append(sample.lab_number)

    # Bulk-remove related notifications for all deleted samples
    notif_patterns = [f'%/samples/{sid}%' for sid in sample_ids]
    for pattern in notif_patterns:
        Notification.query.filter(
            Notification.link.like(pattern)
        ).delete(synchronize_session=False)

    db.session.commit()

    count = len(deleted_labels)
    flash(
        f'{count} sample{"s" if count != 1 else ""} deleted: '
        f'{", ".join(deleted_labels)}.',
        'success',
    )
    return redirect(url_for('samples.sample_list'))


def _delete_sample_files(sample):
    """Remove all uploaded files associated with a sample from disk."""
    upload_folder = current_app.config.get('UPLOAD_FOLDER')
    if not upload_folder:
        return

    paths_to_remove = set()

    # Scanned file on the sample itself
    if sample.scanned_file:
        paths_to_remove.add(sample.scanned_file)
    if sample.summary_report_file:
        paths_to_remove.add(sample.summary_report_file)
    if sample.certificate_file:
        paths_to_remove.add(sample.certificate_file)

    # Assignment report files
    for assignment in sample.assignments.all():
        if assignment.report_file:
            paths_to_remove.add(assignment.report_file)

    # Supporting documents
    for doc in sample.supporting_documents.all():
        if doc.file_path:
            paths_to_remove.add(doc.file_path)

    # Sample images
    for img in sample.images.all():
        if img.file_path:
            paths_to_remove.add(img.file_path)

    # Document versions
    for dv in sample.document_versions.all():
        if dv.file_path:
            paths_to_remove.add(dv.file_path)

    for filename in paths_to_remove:
        full_path = os.path.join(upload_folder, filename)
        if os.path.isfile(full_path):
            try:
                os.remove(full_path)
            except OSError:
                current_app.logger.warning(
                    'Could not remove file %s during sample deletion', full_path
                )
        # Remove cached PDF conversion if this was a Word document
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext in ('doc', 'docx'):
            base_filename = os.path.splitext(filename)[0]
            cached_pdf = os.path.join(upload_folder, 'pdf_cache', base_filename + '.pdf')
            if os.path.isfile(cached_pdf):
                try:
                    os.remove(cached_pdf)
                except OSError:
                    current_app.logger.warning(
                        'Could not remove cached PDF %s during sample deletion', cached_pdf
                    )


# ---------------------------------------------------------------------------
# COA Decertify / Re-Issue  (Feature 5)
# ---------------------------------------------------------------------------

def _can_manage_coa(user):
    """Return True if user is authorised to decertify/re-issue COAs."""
    return (
        user.has_any_role(Role.HOD, Role.ADMIN)
        or user.has_permission(Permission.COA_DECERTIFY_REISSUE)
    )


@samples_bp.route('/<int:sample_id>/coa/decertify', methods=['GET', 'POST'])
@login_required
def coa_decertify(sample_id):
    """Decertify a signed COA (HOD / Government Chemist only)."""
    sample = db.get_or_404(Sample, sample_id)
    if not _can_manage_coa(current_user):
        flash('Access denied. Only HOD or Government Chemist can decertify COAs.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    if sample.status != SampleStatus.CERTIFIED:
        flash('Only certified samples can be decertified.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    form = COADecertifyForm()
    if form.validate_on_submit():
        now = jamaica_now()
        sample.decertified_at = now
        sample.decertified_by = current_user.id
        sample.decertify_reason = form.reason.data
        sample.status = SampleStatus.CERTIFICATE_PREPARATION  # back to prep stage

        _add_history(
            sample, 'COA Decertified',
            f'Decertified by {current_user.full_name}: {form.reason.data}',
            action_type='Decertify',
            object_affected='COA',
            change_description=form.reason.data,
        )
        db.session.add(AuditLog(
            action='COA_DECERTIFY',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=f'Reason: {form.reason.data}',
            performed_by=current_user.id,
        ))
        db.session.commit()
        flash('COA decertified. Sample returned to Certificate Preparation stage.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    return render_template('samples/coa_decertify.html', form=form, sample=sample)


@samples_bp.route('/<int:sample_id>/coa/reissue', methods=['GET', 'POST'])
@login_required
def coa_reissue(sample_id):
    """Re-issue a Certificate of Analysis (HOD / Government Chemist only)."""
    sample = db.get_or_404(Sample, sample_id)
    if not _can_manage_coa(current_user):
        flash('Access denied. Only HOD or Government Chemist can re-issue COAs.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    # Can only re-issue if currently in CERTIFICATE_PREPARATION after a decertify
    if sample.status not in (SampleStatus.CERTIFICATE_PREPARATION, SampleStatus.CERTIFIED):
        flash('Sample is not in a state that permits COA re-issue.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    form = COAReissueForm()
    if form.validate_on_submit():
        now = jamaica_now()
        # Archive previous cert details in history before overwriting
        prev_ref = sample.coa_reference
        prev_ver = sample.coa_version or 1

        if form.certificate_file.data:
            stored, original = _save_file(form.certificate_file.data)
            sample.certificate_file = stored
            sample.certificate_file_original_name = original
            existing_versions = DocumentVersion.query.filter_by(
                sample_id=sample.id, document_type='certificate'
            ).count()
            db.session.add(DocumentVersion(
                sample_id=sample.id,
                document_type='certificate',
                version_number=existing_versions + 1,
                file_path=stored,
                original_name=original,
                upload_label='reissued',
                uploaded_by=current_user.id,
            ))

        sample.certificate_text = form.certificate_text.data
        if form.coa_reference.data:
            sample.coa_reference = form.coa_reference.data
        sample.coa_version = prev_ver + 1
        sample.reissued_at = now
        sample.reissued_by = current_user.id
        sample.status = SampleStatus.CERTIFIED
        sample.certified_at = now
        sample.certified_by = current_user.id

        _add_history(
            sample, 'COA Re-Issued',
            (f'Re-issued by {current_user.full_name}. '
             f'Previous version: {prev_ver}, Previous ref: {prev_ref}. '
             f'New ref: {sample.coa_reference}'),
            action_type='Reissue',
            object_affected='COA',
        )
        db.session.add(AuditLog(
            action='COA_REISSUE',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=f'Version: {sample.coa_version}, Ref: {sample.coa_reference}',
            performed_by=current_user.id,
        ))
        db.session.commit()
        flash(f'COA re-issued successfully (Version {sample.coa_version}).', 'success')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    # Pre-populate with current certificate text
    if request.method == 'GET':
        form.certificate_text.data = sample.certificate_text
        form.coa_reference.data = sample.coa_reference

    return render_template('samples/coa_reissue.html', form=form, sample=sample)


# ---------------------------------------------------------------------------
# Post-Certification Out-of-Spec Marking
# ---------------------------------------------------------------------------

def _can_mark_oos(user):
    """Return True if user is authorised to mark a certified sample OOS.

    Permission-based: available to HOD, Deputy, or Senior Chemist by role,
    or to any user explicitly granted Permission.OOS_FLAG.
    """
    return (
        user.has_any_role(Role.HOD, Role.DEPUTY, Role.SENIOR_CHEMIST, Role.ADMIN)
        or user.has_permission(Permission.OOS_FLAG)
    )


@samples_bp.route('/<int:sample_id>/mark-out-of-spec', methods=['GET', 'POST'])
@login_required
def mark_out_of_spec(sample_id):
    """Flag a certified sample as out-of-spec (HOD / Deputy / Senior Chemist)."""
    sample = db.get_or_404(Sample, sample_id)
    if not _can_mark_oos(current_user):
        flash('Access denied. Only HOD, Deputy, or Senior Chemist can mark a '
              'sample as out of spec.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    if sample.status != SampleStatus.CERTIFIED:
        flash('Only certified samples can be marked as out of spec.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    if sample.marked_oos_at:
        flash('This sample has already been marked as out of spec.', 'info')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    form = MarkOutOfSpecForm()
    if form.validate_on_submit():
        now = jamaica_now()
        sample.marked_oos_at = now
        sample.marked_oos_by = current_user.id
        sample.oos_mark_reason = form.reason.data

        _add_history(
            sample, 'Marked Out of Spec',
            f'Marked out of spec by {current_user.full_name}: {form.reason.data}',
            action_type='Mark Out of Spec',
            object_affected='Sample',
            change_description=form.reason.data,
        )
        db.session.add(AuditLog(
            action='SAMPLE_MARK_OUT_OF_SPEC',
            entity_type='Sample',
            entity_id=sample.id,
            entity_label=sample.lab_number,
            details=f'Reason: {form.reason.data}',
            performed_by=current_user.id,
        ))
        db.session.commit()
        notify_marked_out_of_spec(sample)
        flash('Sample marked as out of spec.', 'success')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    return render_template('samples/mark_out_of_spec.html', form=form, sample=sample)


# ---------------------------------------------------------------------------
# Invoice routes  (Feature 9)
# ---------------------------------------------------------------------------

def _next_invoice_number():
    """Generate a sequential invoice number INV-YYYY-NNNN."""
    from datetime import date
    year = date.today().year
    prefix = f'INV-{year}-'
    last = Invoice.query.filter(
        Invoice.invoice_number.like(f'{prefix}%')
    ).order_by(Invoice.id.desc()).first()
    if last:
        try:
            seq = int(last.invoice_number[len(prefix):]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f'{prefix}{seq:04d}'


def _can_manage_invoices():
    """Return True if the current user may generate/edit/delete invoices."""
    return (
        current_user.has_any_role(Role.ADMIN, Role.HOD, Role.OFFICER)
        or current_user.has_permission(Permission.INVOICE_GENERATE)
    )


def _rebuild_invoice_items(invoice):
    """Replace an invoice's line items with those parsed from the request form."""
    names = request.form.getlist('item_test_name')
    types = request.form.getlist('item_test_type')
    costs = request.form.getlist('item_unit_cost')
    qtys = request.form.getlist('item_quantity')

    for i, name in enumerate(names):
        if not name.strip():
            continue
        try:
            unit_cost = float(costs[i]) if i < len(costs) else 0
        except (ValueError, IndexError):
            unit_cost = 0
        try:
            qty = int(qtys[i]) if i < len(qtys) else 1
            qty = max(1, qty)
        except (ValueError, IndexError):
            qty = 1
        test_type = types[i] if i < len(types) else ''

        db.session.add(InvoiceItem(
            invoice_id=invoice.id,
            test_name=name.strip(),
            test_type=test_type or None,
            unit_cost=unit_cost,
            quantity=qty,
        ))


@samples_bp.route('/<int:sample_id>/invoice/new', methods=['GET', 'POST'])
@login_required
def invoice_create(sample_id):
    """Create a new invoice for a sample."""
    sample = db.get_or_404(Sample, sample_id)
    if not _can_manage_invoices():
        flash('Access denied. You are not permitted to generate invoices.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample_id))

    assignments = sample.assignments.order_by(
        SampleAssignment.assigned_date.desc()
    ).all()
    form = InvoiceCreateForm()
    if form.validate_on_submit():
        invoice = Invoice(
            sample_id=sample.id,
            invoice_number=_next_invoice_number(),
            created_by=current_user.id,
            notes=form.notes.data or None,
        )
        db.session.add(invoice)
        db.session.flush()

        _rebuild_invoice_items(invoice)

        _add_history(
            sample, 'Invoice Created',
            f'Invoice {invoice.invoice_number} created by {current_user.full_name}',
            action_type='Invoice',
            object_affected='Invoice',
        )
        db.session.commit()
        flash(f'Invoice {invoice.invoice_number} created successfully.', 'success')
        return redirect(url_for('samples.invoice_detail',
                                sample_id=sample_id, invoice_id=invoice.id))

    # Build pricing map for JS auto-populate
    pricing_json = json.dumps(PHARMA_TEST_PRICES)
    return render_template(
        'samples/invoice_create.html',
        form=form, sample=sample,
        assignments=assignments,
        pricing_json=pricing_json,
    )


@samples_bp.route('/<int:sample_id>/invoice/<int:invoice_id>')
@login_required
def invoice_detail(sample_id, invoice_id):
    """View an invoice."""
    sample = db.get_or_404(Sample, sample_id)
    invoice = db.get_or_404(Invoice, invoice_id)
    if invoice.sample_id != sample.id:
        abort(404)
    items = invoice.items.all()
    assignments = sample.assignments.order_by(
        SampleAssignment.assigned_date.desc()
    ).all()
    grand_total = sum(item.line_total for item in items)
    return render_template(
        'samples/invoice_detail.html',
        sample=sample, invoice=invoice, items=items, assignments=assignments,
        grand_total=grand_total,
        can_manage_invoices=_can_manage_invoices(),
    )


@samples_bp.route('/<int:sample_id>/invoice/<int:invoice_id>/edit',
                  methods=['GET', 'POST'])
@login_required
def invoice_edit(sample_id, invoice_id):
    """Edit an existing invoice."""
    sample = db.get_or_404(Sample, sample_id)
    invoice = db.get_or_404(Invoice, invoice_id)
    if invoice.sample_id != sample.id:
        abort(404)
    if not _can_manage_invoices():
        flash('Access denied. You are not permitted to edit invoices.', 'danger')
        return redirect(url_for('samples.invoice_detail',
                                sample_id=sample_id, invoice_id=invoice_id))

    assignments = sample.assignments.order_by(
        SampleAssignment.assigned_date.desc()
    ).all()
    form = InvoiceCreateForm(obj=invoice)
    form.submit.label.text = 'Save Changes'
    if form.validate_on_submit():
        invoice.notes = form.notes.data or None

        # Replace all existing line items with the submitted ones
        for item in invoice.items.all():
            db.session.delete(item)
        db.session.flush()
        _rebuild_invoice_items(invoice)

        _add_history(
            sample, 'Invoice Updated',
            f'Invoice {invoice.invoice_number} updated by {current_user.full_name}',
            action_type='Invoice',
            object_affected='Invoice',
        )
        db.session.commit()
        flash(f'Invoice {invoice.invoice_number} updated successfully.', 'success')
        return redirect(url_for('samples.invoice_detail',
                                sample_id=sample_id, invoice_id=invoice.id))

    # Build pricing map for JS auto-populate
    pricing_json = json.dumps(PHARMA_TEST_PRICES)
    items = invoice.items.all()
    return render_template(
        'samples/invoice_edit.html',
        form=form, sample=sample, invoice=invoice,
        items=items, assignments=assignments,
        pricing_json=pricing_json,
    )


@samples_bp.route('/<int:sample_id>/invoice/<int:invoice_id>/delete',
                  methods=['POST'])
@login_required
def invoice_delete(sample_id, invoice_id):
    """Delete an invoice."""
    sample = db.get_or_404(Sample, sample_id)
    invoice = db.get_or_404(Invoice, invoice_id)
    if invoice.sample_id != sample.id:
        abort(404)
    if not _can_manage_invoices():
        flash('Access denied. You are not permitted to delete invoices.', 'danger')
        return redirect(url_for('samples.invoice_detail',
                                sample_id=sample_id, invoice_id=invoice_id))

    invoice_number = invoice.invoice_number
    db.session.delete(invoice)
    _add_history(
        sample, 'Invoice Deleted',
        f'Invoice {invoice_number} deleted by {current_user.full_name}',
        action_type='Invoice',
        object_affected='Invoice',
    )
    db.session.commit()
    flash(f'Invoice {invoice_number} deleted successfully.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample_id))


# ---------------------------------------------------------------------------
# HOD Workflow Return — return any report at any stage
# ---------------------------------------------------------------------------

# Valid return targets mapped by current status
HOD_RETURN_TARGETS = {
    SampleStatus.ASSIGNED: [SampleStatus.REGISTERED],
    SampleStatus.IN_PROGRESS: [SampleStatus.REGISTERED, SampleStatus.ASSIGNED],
    SampleStatus.REPORT_SUBMITTED: [SampleStatus.ASSIGNED, SampleStatus.IN_PROGRESS],
    SampleStatus.UNDER_PRELIMINARY_REVIEW: [
        SampleStatus.ASSIGNED, SampleStatus.IN_PROGRESS,
        SampleStatus.REPORT_SUBMITTED,
    ],
    SampleStatus.UNDER_TECHNICAL_REVIEW: [
        SampleStatus.ASSIGNED, SampleStatus.REPORT_SUBMITTED,
        SampleStatus.UNDER_PRELIMINARY_REVIEW,
    ],
    SampleStatus.ACCEPTED: [
        SampleStatus.ASSIGNED, SampleStatus.UNDER_TECHNICAL_REVIEW,
    ],
    SampleStatus.DEPUTY_REVIEW: [
        SampleStatus.ASSIGNED, SampleStatus.ACCEPTED,
        SampleStatus.UNDER_TECHNICAL_REVIEW,
    ],
    SampleStatus.CERTIFICATE_PREPARATION: [
        SampleStatus.DEPUTY_REVIEW, SampleStatus.ACCEPTED,
    ],
    SampleStatus.HOD_REVIEW: [
        SampleStatus.DEPUTY_REVIEW, SampleStatus.CERTIFICATE_PREPARATION,
        SampleStatus.ACCEPTED,
    ],
}


@samples_bp.route('/sample/<int:sample_id>/hod-return', methods=['POST'])
@login_required
def hod_workflow_return(sample_id):
    """HOD returns a report to any valid prior workflow stage."""
    sample = db.get_or_404(Sample, sample_id)

    # Permission check: must be HOD or have HOD_WORKFLOW_RETURN permission
    if not (current_user.has_role(Role.HOD)
            or current_user.has_permission(Permission.HOD_WORKFLOW_RETURN)
            or current_user.has_role(Role.ADMIN)):
        flash('Only the HOD can return reports across workflow stages.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    # Non-returnable statuses
    non_returnable = {
        SampleStatus.REGISTERED, SampleStatus.CERTIFIED,
        SampleStatus.COMPLETED, SampleStatus.REJECTED,
    }
    if sample.status in non_returnable:
        flash('This report cannot be returned from its current state.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    target_stage_str = request.form.get('target_stage', '').strip()
    reason = request.form.get('return_reason', '').strip()

    # Validate reason
    if not reason or len(reason) < 10:
        flash('Please provide a detailed reason (minimum 10 characters).', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    # Validate target stage
    valid_targets = HOD_RETURN_TARGETS.get(sample.status, [])
    target_status = None
    for status in valid_targets:
        if status.name == target_stage_str:
            target_status = status
            break

    if target_status is None:
        flash('Invalid return target stage.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    old_status = sample.status
    old_status_value = old_status.value

    # Update sample
    sample.status = target_status
    sample.returned_to_stage = target_status.name
    sample.hod_return_reason = reason
    sample.returned_by_hod_at = jamaica_now()

    # If returning to analyst-level stage, reset assignments
    analyst_stages = {SampleStatus.ASSIGNED, SampleStatus.IN_PROGRESS,
                      SampleStatus.REPORT_SUBMITTED}
    if target_status in analyst_stages:
        for assignment in sample.assignments.all():
            if assignment.status not in (AssignmentStatus.RETURNED,
                                         AssignmentStatus.ASSIGNED):
                assignment.status = AssignmentStatus.RETURNED
                assignment.return_stage = 'hod'

    # Record history
    _add_history(
        sample,
        'HOD Workflow Return',
        (f'{current_user.full_name} (HOD) returned report from '
         f'"{old_status_value}" to "{target_status.value}". '
         f'Reason: {reason}'),
        action_type='HOD Return',
        object_affected='Sample',
        change_description=(
            f'Report returned by HOD from {old_status_value} to '
            f'{target_status.value}. Reason: {reason}'),
    )

    # Record in ReviewHistory
    db.session.add(ReviewHistory(
        sample_id=sample.id,
        review_type='hod',
        review_number=1,
        action='returned',
        reviewer_id=current_user.id,
        reviewed_at=jamaica_now(),
        comments=reason,
    ))

    # Record in AuditLog with enhanced fields
    user_role_str = ''
    if current_user.roles:
        user_role_str = ', '.join(r.value for r in current_user.roles)
    user_branch_str = ''
    if current_user.branches:
        user_branch_str = ', '.join(b.value for b in current_user.branches)

    human_desc = (
        f'{current_user.full_name}, {user_role_str or "HOD"}, returned '
        f'{sample.sample_type.value} Report #{sample.lab_number} from '
        f"'{old_status_value}' to '{target_status.value}' on "
        f'{jamaica_now().strftime("%d %B %Y at %I:%M %p")}. '
        f'Reason: {reason}'
    )

    db.session.add(AuditLog(
        action='HOD_WORKFLOW_RETURN',
        entity_type='Sample',
        entity_id=sample.id,
        entity_label=sample.lab_number,
        details=json.dumps({
            'lab_number': sample.lab_number,
            'sample_name': sample.sample_name,
            'previous_status': old_status_value,
            'new_status': target_status.value,
            'returned_by': current_user.full_name,
            'reason': reason,
        }),
        performed_by=current_user.id,
        human_description=human_desc,
        user_role=user_role_str,
        user_department=user_branch_str,
        report_type=sample.sample_type.value if sample.sample_type else None,
        previous_stage=old_status.name,
        new_stage=target_status.name,
        comments=reason,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent', '')[:500],
        success=True,
    ))

    db.session.commit()

    # Notify relevant users
    from app.notifications import create_notification

    # Determine who to notify based on target stage
    recipients = set()
    if target_status in analyst_stages:
        # Notify all assigned chemists
        for assignment in sample.assignments.all():
            if assignment.chemist_id:
                recipients.add(assignment.chemist_id)
    elif target_status == SampleStatus.UNDER_PRELIMINARY_REVIEW:
        # Notify officers
        officer_users = User.query.join(user_roles).filter(
            user_roles.c.role == Role.OFFICER
        ).all()
        for u in officer_users:
            recipients.add(u.id)
    elif target_status == SampleStatus.UNDER_TECHNICAL_REVIEW:
        # Notify senior chemists
        sc_users = User.query.join(user_roles).filter(
            user_roles.c.role == Role.SENIOR_CHEMIST
        ).all()
        for u in sc_users:
            recipients.add(u.id)
    elif target_status in (SampleStatus.DEPUTY_REVIEW,
                           SampleStatus.CERTIFICATE_PREPARATION):
        # Notify deputies
        deputy_users = User.query.join(user_roles).filter(
            user_roles.c.role == Role.DEPUTY
        ).all()
        for u in deputy_users:
            recipients.add(u.id)
    elif target_status == SampleStatus.ACCEPTED:
        # Notify senior chemists in branch
        sc_users = User.query.join(user_roles).filter(
            user_roles.c.role == Role.SENIOR_CHEMIST
        ).all()
        for u in sc_users:
            recipients.add(u.id)

    for user_id in recipients:
        if user_id != current_user.id:
            notif_message = (
                f'Report "{sample.sample_name}" (Lab# {sample.lab_number}) has been '
                f'returned by HOD from "{old_status_value}" to '
                f'"{target_status.value}".\n\nReason: {reason}'
            )
            create_notification(
                user_id,
                f'Report Returned by HOD: {sample.lab_number}',
                notif_message,
                f'/samples/{sample.id}',
            )
    db.session.commit()

    flash(
        f'Report returned from "{old_status_value}" to "{target_status.value}".',
        'success',
    )
    return redirect(url_for('samples.detail', sample_id=sample.id))


# ---------------------------------------------------------------------------
# Sample Comments — add, edit, delete comments on a sample
# ---------------------------------------------------------------------------

@samples_bp.route('/sample/<int:sample_id>/comments', methods=['POST'])
@login_required
def add_comment(sample_id):
    """Add a comment to a sample."""
    sample = db.get_or_404(Sample, sample_id)

    # Permission check
    if not (current_user.has_permission(Permission.SAMPLE_COMMENT_ADD)
            or current_user.has_any_role(Role.ADMIN, Role.SUPER_ADMIN,
                                         Role.HOD, Role.DEPUTY,
                                         Role.SENIOR_CHEMIST, Role.CHEMIST,
                                         Role.OFFICER)):
        flash('You do not have permission to add comments.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    comment_text = request.form.get('comment_text', '').strip()
    if not comment_text:
        flash('Comment cannot be empty.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    if len(comment_text) > 5000:
        flash('Comment exceeds maximum length of 5000 characters.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    comment = SampleComment(
        sample_id=sample.id,
        user_id=current_user.id,
        comment_text=comment_text,
    )
    db.session.add(comment)

    # Audit log
    db.session.add(AuditLog(
        action='COMMENT_ADDED',
        entity_type='SampleComment',
        entity_id=sample.id,
        entity_label=sample.lab_number,
        details=json.dumps({
            'sample_lab_number': sample.lab_number,
            'comment_by': current_user.full_name,
            'comment_text': comment_text[:200],
        }),
        performed_by=current_user.id,
        human_description=(
            f'{current_user.full_name} added a comment on '
            f'{sample.sample_type.value} Sample #{sample.lab_number}.'
        ),
        report_type=sample.sample_type.value if sample.sample_type else None,
        ip_address=request.remote_addr,
        success=True,
    ))

    db.session.commit()
    flash('Comment added.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id))


@samples_bp.route('/sample/<int:sample_id>/comments/<int:comment_id>/edit',
                  methods=['POST'])
@login_required
def edit_comment(sample_id, comment_id):
    """Edit a sample comment."""
    sample = db.get_or_404(Sample, sample_id)
    comment = db.get_or_404(SampleComment, comment_id)

    if comment.sample_id != sample.id:
        abort(404)

    # Permission: own comment or admin
    is_owner = (comment.user_id == current_user.id)
    is_admin = current_user.has_any_role(Role.ADMIN, Role.SUPER_ADMIN)
    has_edit_perm = current_user.has_permission(Permission.SAMPLE_COMMENT_EDIT)

    if not (is_owner or is_admin or has_edit_perm):
        flash('You do not have permission to edit this comment.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    new_text = request.form.get('comment_text', '').strip()
    if not new_text:
        flash('Comment cannot be empty.', 'warning')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    if len(new_text) > 5000:
        flash('Comment exceeds maximum length of 5000 characters.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    old_text = comment.comment_text
    comment.comment_text = new_text
    comment.updated_at = jamaica_now()

    db.session.add(AuditLog(
        action='COMMENT_EDITED',
        entity_type='SampleComment',
        entity_id=comment.id,
        entity_label=sample.lab_number,
        details=json.dumps({
            'sample_lab_number': sample.lab_number,
            'edited_by': current_user.full_name,
        }),
        performed_by=current_user.id,
        human_description=(
            f'{current_user.full_name} edited a comment on '
            f'Sample #{sample.lab_number}.'
        ),
        previous_value=old_text[:500],
        new_value=new_text[:500],
        ip_address=request.remote_addr,
        success=True,
    ))

    db.session.commit()
    flash('Comment updated.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id))


@samples_bp.route('/sample/<int:sample_id>/comments/<int:comment_id>/delete',
                  methods=['POST'])
@login_required
def delete_comment(sample_id, comment_id):
    """Soft-delete a sample comment."""
    sample = db.get_or_404(Sample, sample_id)
    comment = db.get_or_404(SampleComment, comment_id)

    if comment.sample_id != sample.id:
        abort(404)

    # Permission: admin only
    if not (current_user.has_any_role(Role.ADMIN, Role.SUPER_ADMIN)
            or current_user.has_permission(Permission.SAMPLE_COMMENT_DELETE)):
        flash('You do not have permission to delete comments.', 'danger')
        return redirect(url_for('samples.detail', sample_id=sample.id))

    comment.is_deleted = True
    comment.deleted_by = current_user.id
    comment.deleted_at = jamaica_now()

    db.session.add(AuditLog(
        action='COMMENT_DELETED',
        entity_type='SampleComment',
        entity_id=comment.id,
        entity_label=sample.lab_number,
        details=json.dumps({
            'sample_lab_number': sample.lab_number,
            'deleted_by': current_user.full_name,
            'original_text': comment.comment_text[:200],
        }),
        performed_by=current_user.id,
        human_description=(
            f'{current_user.full_name} deleted a comment on '
            f'Sample #{sample.lab_number}.'
        ),
        ip_address=request.remote_addr,
        success=True,
    ))

    db.session.commit()
    flash('Comment deleted.', 'success')
    return redirect(url_for('samples.detail', sample_id=sample.id))
