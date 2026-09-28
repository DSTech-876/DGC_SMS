"""Regression tests: a returned report must be re-uploadable with the SAME
filename — the filename is never the determining factor; each upload is
stored under a unique key and versioned by content."""
import io

from app import db
from app.models import (
    Sample, SampleAssignment, AssignmentStatus, DocumentVersion,
)
from tests.conftest import _login
from tests.test_samples import _setup_users, _register_sample, _MINIMAL_PDF

_REVISED_PDF = _MINIMAL_PDF.replace(b'612 792', b'595 842')  # different content


def _upload(name, content=_MINIMAL_PDF):
    return (io.BytesIO(content), name)


def test_resubmit_report_with_same_filename_succeeds(app, client):
    """Upload → return for revision → re-upload with the identical filename
    but new content must be accepted and stored as a new version."""
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

    # Initial upload: WaterAnalysis.pdf
    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Initial findings.',
        'report_file': _upload('WaterAnalysis.pdf'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert b'submitted successfully' in resp.data
    client.get('/auth/logout')

    # Returned for revision
    _login(client, 'officer')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    client.post(f'/samples/assignment/{assignment.id}/preliminary-review', data={
        'action': 'returned',
        'review_comments': 'Please correct section 2.',
    }, follow_redirects=True)
    client.get('/auth/logout')

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.RETURNED

    # Re-upload with the SAME filename but new content — must be accepted.
    _login(client, 'chemist')
    with app.app_context():
        assignment = SampleAssignment.query.first()
    resp = client.post(f'/samples/assignment/{assignment.id}/report', data={
        'report_text': 'Corrected findings.',
        'report_file': _upload('WaterAnalysis.pdf', _REVISED_PDF),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert b'submitted successfully' in resp.data

    with app.app_context():
        assignment = SampleAssignment.query.first()
        assert assignment.status == AssignmentStatus.REPORT_SUBMITTED
        assert assignment.report_file_original_name == 'WaterAnalysis.pdf'

        versions = DocumentVersion.query.filter_by(
            sample_id=assignment.sample_id, document_type='report',
            assignment_id=assignment.id,
        ).order_by(DocumentVersion.version_number).all()
        assert [v.version_number for v in versions] == [1, 2]
        # Same display name is allowed on both versions...
        assert versions[0].original_name == 'WaterAnalysis.pdf'
        assert versions[1].original_name == 'WaterAnalysis.pdf'
        # ...because each upload is stored under a unique key (no collisions).
        assert versions[0].file_path != versions[1].file_path
        assert versions[1].upload_label == 'resubmission'
