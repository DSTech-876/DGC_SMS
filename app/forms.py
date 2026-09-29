"""WTForms for the DGC Samples Management System."""

from datetime import date as date_today
from markupsafe import Markup
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (
    StringField, PasswordField, SelectField, TextAreaField,
    DateField, SubmitField, BooleanField, SelectMultipleField,
    RadioField, IntegerField, widgets,
)
from wtforms.validators import (
    DataRequired, Email, EqualTo, Length, Optional, ValidationError,
    Regexp, NumberRange,
)

from app.models import Role, Branch, Permission, User


def _strong_password(form, field):
    """Ensure password meets complexity requirements."""
    pw = field.data or ''
    if not pw:
        return  # skip if empty (Optional fields)
    errors = []
    if not any(c.isupper() for c in pw):
        errors.append('one uppercase letter')
    if not any(c.islower() for c in pw):
        errors.append('one lowercase letter')
    if not any(c.isdigit() for c in pw):
        errors.append('one digit')
    if errors:
        raise ValidationError(
            'Password must contain at least: ' + ', '.join(errors) + '.'
        )


def _sort_choices(choices):
    """Return a choices list sorted A-Z by label, keeping blank option(s) first."""
    blank = [c for c in choices if not c[0]]
    rest = sorted([c for c in choices if c[0]], key=lambda c: c[1].lower())
    return blank + rest


FORMULATION_TYPE_CHOICES = _sort_choices([
    ('', '-- Select Formulation --'),
    ('Capsule', 'Capsule'),
    ('Tablet', 'Tablet'),
    ('Cream', 'Cream'),
    ('Ointment', 'Ointment'),
    ('Oral Solution', 'Oral Solution'),
    ('Suspension', 'Suspension'),
    ('Solution', 'Solution'),
    ('Injection', 'Injection'),
    ('Powder', 'Powder'),
])

# Active Pharmaceutical Ingredient (API) choices (Feature 7)
API_CHOICES = _sort_choices([
    ('', '-- Select API --'),
    ('Paracetamol', 'Paracetamol'),
    ('Diphenhydramine Hydrochloride', 'Diphenhydramine Hydrochloride'),
    ('Aripiprazole', 'Aripiprazole'),
    ('Sulphur', 'Sulphur'),
    ('Ethyl Alcohol', 'Ethyl Alcohol'),
    ('Menthol', 'Menthol'),
    ('Metformin Hydrochloride', 'Metformin Hydrochloride'),
    ('Sitagliptin Phosphate', 'Sitagliptin Phosphate'),
    ('Dabigatran Etexilate', 'Dabigatran Etexilate'),
    ('Linagliptin', 'Linagliptin'),
    ('Aspirin', 'Aspirin'),
    ('Amlodipine Besylate', 'Amlodipine Besylate'),
    ('Tolperisone Hydrochloride', 'Tolperisone Hydrochloride'),
    ('Salbutamol Sulphate', 'Salbutamol Sulphate'),
    ('Azithromycin', 'Azithromycin'),
    ('Albendazole', 'Albendazole'),
    ('Monobasic Sodium Phosphate', 'Monobasic Sodium Phosphate'),
    ('Dibasic Sodium Phosphate', 'Dibasic Sodium Phosphate'),
    ('Vildagliptin', 'Vildagliptin'),
    ('Caffeine', 'Caffeine'),
    ('Pyrilamine Maleate', 'Pyrilamine Maleate'),
    ('Chlorpheniramine Maleate', 'Chlorpheniramine Maleate'),
    ('Dextromethorphan Hydrobromide', 'Dextromethorphan Hydrobromide'),
    ('Guaiphenesin', 'Guaiphenesin'),
    ('Pseudoephedrine Hydrochloride', 'Pseudoephedrine Hydrochloride'),
    ('Tetracycline Hydrochloride', 'Tetracycline Hydrochloride'),
    ('Amoxicillin Trihydrate', 'Amoxicillin Trihydrate'),
    ('Hydrogen Peroxide', 'Hydrogen Peroxide'),
    ('Pirfenidone', 'Pirfenidone'),
    ('Sodium Chloride', 'Sodium Chloride'),
    ('Dextrose', 'Dextrose'),
    ('Codeine Phosphate', 'Codeine Phosphate'),
    ('Acetaminophen', 'Acetaminophen'),
    ('Ammonium Chloride', 'Ammonium Chloride'),
    ('Sodium Citrate', 'Sodium Citrate'),
    ('Hydrocortisone', 'Hydrocortisone'),
    ('Phenobarbitone', 'Phenobarbitone'),
    ('Diclofenac Sodium', 'Diclofenac Sodium'),
    ('Potassium Sorbate', 'Potassium Sorbate'),
    ('Sodium Zirconium Cyclosilicate', 'Sodium Zirconium Cyclosilicate'),
    ('Betamethasone', 'Betamethasone'),
    ('Losartan Potassium', 'Losartan Potassium'),
    ('Levamisole', 'Levamisole'),
    ('Timolol Maleate', 'Timolol Maleate'),
    ('Brimonidine Tartrate', 'Brimonidine Tartrate'),
    ('Methyl Salicylate', 'Methyl Salicylate'),
    ('Glycerol', 'Glycerol'),
    ('Phenylephrine', 'Phenylephrine'),
    ('Glycerine', 'Glycerine'),
    ('Sacubitril', 'Sacubitril'),
    ('Valsartan', 'Valsartan'),
    ('Magnesium Trisilicate', 'Magnesium Trisilicate'),
    ('Brinzolamide', 'Brinzolamide'),
    ('Thiamine Hydrochloride', 'Thiamine Hydrochloride'),
    ('Riboflavin', 'Riboflavin'),
    ('Nicotinamide', 'Nicotinamide'),
    ('Pyridoxine Hydrochloride', 'Pyridoxine Hydrochloride'),
    ('Calcium Pantothenate', 'Calcium Pantothenate'),
    ('Butylated Hydroxytoluene', 'Butylated Hydroxytoluene'),
    ('Ascorbic Acid', 'Ascorbic Acid'),
    ('Diazepam', 'Diazepam'),
    ('Mercurochrome', 'Mercurochrome'),
    ('Sodium Bicarbonate', 'Sodium Bicarbonate'),
    ('Magnesium Carbonate', 'Magnesium Carbonate'),
    ('Fennel Oil', 'Fennel Oil'),
    ('Dill Oil', 'Dill Oil'),
    ('Light Magnesium Carbonate', 'Light Magnesium Carbonate'),
    ('Telmisartan', 'Telmisartan'),
    ('Rivaroxaban', 'Rivaroxaban'),
    ('Potassium Iodide', 'Potassium Iodide'),
    ('Alcohol', 'Alcohol'),
    ('Iodine', 'Iodine'),
    ('Hydrochlorothiazide', 'Hydrochlorothiazide'),
    ('Dapagliflozin', 'Dapagliflozin'),
    ('Trimethoprim', 'Trimethoprim'),
    ('Sulfamethoxazole', 'Sulfamethoxazole'),
    ('Teriflunomide', 'Teriflunomide'),
    ('Clopidogrel', 'Clopidogrel'),
    ('Cefixime', 'Cefixime'),
    ('Dolutegravir', 'Dolutegravir'),
    ('Emtricitabine', 'Emtricitabine'),
    ('Tenofovir Alafenamide', 'Tenofovir Alafenamide'),
    ('Papain- Urea', 'Papain- Urea'),
    ('Dimenhydrinate', 'Dimenhydrinate'),
    ('Silver Nitrate', 'Silver Nitrate'),
    ('Ciprofloxacin', 'Ciprofloxacin'),
    ('Bromhexine Hydrochloride', 'Bromhexine Hydrochloride'),
    ('Silver Sulfadiazine', 'Silver Sulfadiazine'),
    ('Chlorhexidine Gluconate', 'Chlorhexidine Gluconate'),
    ('Leflunomide', 'Leflunomide'),
    ('Frusemide', 'Frusemide'),
    ('Ivermectin', 'Ivermectin'),
    ('Citric Acid', 'Citric Acid'),
    ('Tranexamic Acid', 'Tranexamic Acid'),
    ('Mefenamic Acid', 'Mefenamic Acid'),
    ('Apixaban', 'Apixaban'),
    ('Camphorated Opium Tinct', 'Camphorated Opium Tinct'),
    ('White Pine', 'White Pine'),
    ('Loratadine', 'Loratadine'),
    ('Clindamycin', 'Clindamycin'),
    ('Clotrimazole', 'Clotrimazole'),
    ('Magnesia', 'Magnesia'),
    ('Simethicone', 'Simethicone'),
    ('Alumina', 'Alumina'),
    ('Gentian Violet', 'Gentian Violet'),
    ('Benzocaine', 'Benzocaine'),
    ('Clove Oil', 'Clove Oil'),
    ('Other', 'Other'),
])

# API choices without blank entry – used for the multi-select field
API_CHOICES_MULTI = [c for c in API_CHOICES if c[0]]

# Toxicology sample type choices (replaces Sample Name for Toxicology)
TOXICOLOGY_SAMPLE_TYPE_CHOICES = _sort_choices([
    ('', '-- Select Sample Type --'),
    ('Blood', 'Blood'),
    ('Urine', 'Urine'),
    ('Serum', 'Serum'),
    ('Stomach Content', 'Stomach Content'),
    ('Bile', 'Bile'),
    ('Liver', 'Liver'),
    ('Kidney', 'Kidney'),
    ('Fish', 'Fish'),
    ('Food', 'Food'),
    ('Ackee', 'Ackee'),
    ('24 Hour Urine', '24 Hour Urine'),
    ('Gastric Content', 'Gastric Content'),
    ('Vitreous Humor', 'Vitreous Humor'),
])

# Predefined test names per sample type (Branch)
TOXICOLOGY_TEST_NAMES = _sort_choices([
    ('Salicylate Test- Direct', 'Salicylate Test- Direct'),
    ('Salicylate Test- Confirmatory', 'Salicylate Test- Confirmatory'),
    ('Test for Phenothiazine', 'Test for Phenothiazine'),
    ('Toxicology Screen for Urine', 'Toxicology Screen for Urine'),
    ('Toxicology Screen for Blood', 'Toxicology Screen for Blood'),
    ('Toxicology Screen for Serum', 'Toxicology Screen for Serum'),
    ('Toxicology Screen for Stomach Content', 'Toxicology Screen for Stomach Content'),
    ('Drugs of Abuse', 'Drugs of Abuse'),
    ('Volatile Reducing Substances', 'Volatile Reducing Substances'),
    ('Formaldehyde in Fish', 'Formaldehyde in Fish'),
    ('Detection of Isovaleric Acid in Blood', 'Detection of Isovaleric Acid in Blood'),
    ('Investigation for Paraquat/ Diquat', 'Investigation for Paraquat/ Diquat'),
    ('Analysis of Unknown/ Food Samples', 'Analysis of Unknown/ Food Samples'),
    ('Test for Arsenic, Antimony, Bismuth and Mercury', 'Test for Arsenic, Antimony, Bismuth and Mercury'),
])

TOXICOLOGY_TEST_REFERENCES = [
    ("Clarke's Isolation and Identification of Drugs, 2nd Edition",
     "Clarke's Isolation and Identification of Drugs, 2nd Edition"),
    ('DGC SOP', 'DGC SOP'),
]

FOOD_MILK_TEST_NAMES = _sort_choices([
    ('% Fats', '% Fats'),
    ('% SNF', '% SNF'),
    ('% Total Solids', '% Total Solids'),
    ('Other', 'Other'),
])

FOOD_MILK_TEST_REFERENCES = [
    ('Chemical Analysis of Foods, Eighth Edition, Pearson',
     'Chemical Analysis of Foods, Eighth Edition, Pearson'),
    ('DGC Standard Operating Procedure, FAP-002 Revision 01',
     'DGC Standard Operating Procedure, FAP-002 Revision 01'),
]

FOOD_ALCOHOL_TEST_NAMES = _sort_choices([
    ('Assay for Denatonium Benzoate', 'Assay for Denatonium Benzoate'),
    ('Alcohol Content/ Determination', 'Alcohol Content/ Determination'),
])

FOOD_ALCOHOL_TEST_REFERENCES = [
    ('DGC SOP', 'DGC SOP'),
    ('U.S.P.', 'U.S.P.'),
    ('Other', 'Other'),
]

PHARMACEUTICAL_TEST_NAMES = _sort_choices([
    ('Acidity', 'Acidity'),
    ('Alcohol Content/ Determination', 'Alcohol Content/ Determination'),
    ('Assay by HPLC', 'Assay by HPLC'),
    ('Assay by polarimetry', 'Assay by polarimetry'),
    ('Assay by Titration', 'Assay by Titration'),
    ('Assay by UV', 'Assay by UV'),
    ('Assay Potentiometric Titration', 'Assay Potentiometric Titration'),
    ('Average Weight', 'Average Weight'),
    ('Conductivity', 'Conductivity'),
    ('Deliverable Volume Stage 1', 'Deliverable Volume Stage 1'),
    ('Deliverable Volume Stage 2', 'Deliverable Volume Stage 2'),
    ('Density', 'Density'),
    ('Disintegration (Tablets and Capsule)', 'Disintegration (Tablets and Capsule)'),
    ('Dissolution by HPLC Analysis', 'Dissolution by HPLC Analysis'),
    ('Dissolution UV Analysis', 'Dissolution UV Analysis'),
    ('Dose and Uniformity of Dose of Oral Drops', 'Dose and Uniformity of Dose of Oral Drops'),
    ('Identification by Chemical Reaction', 'Identification by Chemical Reaction'),
    ('Identification by HPLC', 'Identification by HPLC'),
    ('Identification by IR', 'Identification by IR'),
    ('Identification by Thin Layer Chromatography (TLC)', 'Identification by Thin Layer Chromatography (TLC)'),
    ('Identification by UV', 'Identification by UV'),
    ('Impurities by TLC', 'Impurities by TLC'),
    ('Impurities by UV', 'Impurities by UV'),
    ('Impurities by HPLC', 'Impurities by HPLC'),
    ('Loss on Drying', 'Loss on Drying'),
    ('Minimum Fill', 'Minimum Fill'),
    ('Neutralizing Capacity by Titration', 'Neutralizing Capacity by Titration'),
    ('Non Volatile matter', 'Non Volatile matter'),
    ('Organic Stabilizer', 'Organic Stabilizer'),
    ('pH', 'pH'),
    ('Related Substances by Thin Layer Chromatography', 'Related Substances by Thin Layer Chromatography'),
    ('Residue on Ignition', 'Residue on Ignition'),
    ('Specific Gravity', 'Specific Gravity'),
    ('TLC', 'TLC'),
    ('Uniformity of Content by HPLC', 'Uniformity of Content by HPLC'),
    ('Uniformity of Content by UV', 'Uniformity of Content by UV'),
    ('Uniformity and Accuracy of Delivered Doses from Multidose Containers',
     'Uniformity and Accuracy of Delivered Doses from Multidose Containers'),
    ('Uniformity of Weight (Capsules and Tablets)', 'Uniformity of Weight (Capsules and Tablets)'),
    ('Weight Variation', 'Weight Variation'),
    ('Weight per mL', 'Weight per mL'),
    ('Other', 'Other'),
])

PHARMACEUTICAL_TEST_REFERENCES = _sort_choices([
    ('U.S.P.', 'U.S.P.'),
    ('B.P.', 'B.P.'),
    ('Manufacturers method', 'Manufacturers method'),
    ('J.P.', 'J.P.'),
    ('I.P.', 'I.P.'),
    ('DGC SOP', 'DGC SOP'),
])

# Map Branch enum names to their predefined test names/references
BRANCH_TEST_NAMES = {
    'TOXICOLOGY': TOXICOLOGY_TEST_NAMES,
    'FOOD_MILK': FOOD_MILK_TEST_NAMES,
    'FOOD_ALCOHOL': FOOD_ALCOHOL_TEST_NAMES,
    'PHARMACEUTICAL': PHARMACEUTICAL_TEST_NAMES,
    'PHARMACEUTICAL_NR': PHARMACEUTICAL_TEST_NAMES,
}

BRANCH_TEST_REFERENCES = {
    'TOXICOLOGY': TOXICOLOGY_TEST_REFERENCES,
    'FOOD_MILK': FOOD_MILK_TEST_REFERENCES,
    'FOOD_ALCOHOL': FOOD_ALCOHOL_TEST_REFERENCES,
    'PHARMACEUTICAL': PHARMACEUTICAL_TEST_REFERENCES,
    'PHARMACEUTICAL_NR': PHARMACEUTICAL_TEST_REFERENCES,
}


# ---------------------------------------------------------------------------
# Auth forms
# ---------------------------------------------------------------------------

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Sign In')


class ForgotPasswordForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Send Reset Link')


class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[
        DataRequired(), Length(min=8), _strong_password,
    ])
    password2 = PasswordField(
        'Confirm Password', validators=[DataRequired(), EqualTo('password')]
    )
    submit = SubmitField('Reset Password')


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    password = PasswordField('New Password', validators=[
        DataRequired(), Length(min=8), _strong_password,
    ])
    password2 = PasswordField(
        'Confirm New Password', validators=[DataRequired(), EqualTo('password')]
    )
    submit = SubmitField('Change Password')


class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()

    def pre_validate(self, form):
        valid = {v for v, _ in self.choices}
        for d in self.data:
            if d not in valid:
                raise ValidationError(f'Invalid choice: {d}')


class UserCreateForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=120)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=120)])
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=120)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField('Password', validators=[
        DataRequired(), Length(min=8), _strong_password,
    ])
    password2 = PasswordField(
        'Confirm Password', validators=[DataRequired(), EqualTo('password')]
    )
    roles = MultiCheckboxField('Roles', choices=[(r.name, r.value) for r in Role])
    branches = MultiCheckboxField(
        'Branches / Labs',
        choices=[(b.name, b.value) for b in Branch],
    )
    permissions = MultiCheckboxField(
        'Extra Permissions',
        choices=[(p.name, p.value) for p in Permission],
    )
    submit = SubmitField('Create User')

    def validate_roles(self, field):
        if not field.data:
            raise ValidationError('Please select at least one role.')

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError('Username already in use.')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('Email already registered.')


class UserEditForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=120)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=120)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=255)])
    roles = MultiCheckboxField('Roles', choices=[(r.name, r.value) for r in Role])
    branches = MultiCheckboxField(
        'Branches / Labs',
        choices=[(b.name, b.value) for b in Branch],
    )
    permissions = MultiCheckboxField(
        'Extra Permissions',
        choices=[(p.name, p.value) for p in Permission],
    )
    is_active_user = BooleanField('Active')

    def validate_roles(self, field):
        if not field.data:
            raise ValidationError('Please select at least one role.')
    new_password = PasswordField('New Password', validators=[
        Optional(), Length(min=8), _strong_password,
    ])
    confirm_password = PasswordField(
        'Confirm New Password',
        validators=[Optional(), EqualTo('new_password', message='Passwords must match.')],
    )
    submit = SubmitField('Update User')


class ActingRoleForm(FlaskForm):
    """Form to assign an acting role to a user with an expiry date."""
    user_id = SelectField('User', coerce=int, validators=[DataRequired()])
    role = SelectField(
        'Acting Role',
        choices=[(r.name, r.value) for r in Role],
        validators=[DataRequired()],
    )
    start_date = DateField('Start Date', validators=[DataRequired()])
    expiry_date = DateField('Expiry Date', validators=[DataRequired()])
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Assign Acting Role')

    def validate_expiry_date(self, field):
        if self.start_date.data and field.data:
            if field.data < self.start_date.data:
                raise ValidationError('Expiry date must be on or after the start date.')


# ---------------------------------------------------------------------------
# Sample forms
# ---------------------------------------------------------------------------

class SampleRegisterForm(FlaskForm):
    lab_number = StringField('Lab Number', validators=[DataRequired(), Length(max=50)])
    sample_name = StringField('Sample Name', validators=[DataRequired(), Length(max=255)])
    sample_type = SelectField(
        'Laboratory',
        choices=[(b.name, b.value) for b in Branch],
        validators=[DataRequired()],
    )
    description = TextAreaField('Description', validators=[Optional()])
    quantity = StringField('Quantity / Volume', validators=[Optional(), Length(max=100)])
    parish = StringField('Parish', validators=[Optional(), Length(max=100)])
    patient_name = StringField('Patient Name (Toxicology)', validators=[Optional(), Length(max=255)])
    source = StringField('Source', validators=[Optional(), Length(max=255)])
    date_received = DateField('Date Received', validators=[DataRequired()])
    expected_report_date = None
    scanned_file = FileField(
        'Submission Form',
        validators=[FileAllowed(
            ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'doc', 'docx'],
            'Only PDF, image, and Word document files allowed.'
        )],
    )
    submit = SubmitField('Register Sample')

    def validate_lab_number(self, field):
        from app.models import Sample
        existing = Sample.query.filter_by(lab_number=field.data).first()
        if existing:
            raise ValidationError('Lab number already exists.')


# ---------------------------------------------------------------------------
# Type-specific registration forms (for different lab types/sample types)
# ---------------------------------------------------------------------------

class ToxicologySampleRegisterForm(SampleRegisterForm):
    """Registration form for Toxicology samples.
    Uses Sample Type dropdown (Blood, Urine, etc.), volume, no parish, no expected report date.
    """
    # Override quantity field to remove it (use volume instead)
    quantity = None
    parish = None
    expected_report_date = None

    # Replace free-text sample_name with Sample Type dropdown
    sample_name = StringField('Sample Name', validators=[DataRequired(), Length(max=255)])
    toxicology_sample_type_name = SelectField(
        'Sample Type',
        choices=TOXICOLOGY_SAMPLE_TYPE_CHOICES,
        validators=[Optional()],
    )

    # Source relabelled as "Hospital" for toxicology
    source = StringField('Hospital', validators=[Optional(), Length(max=255)])

    patient_name = StringField(
        'Patient Name',
        validators=[Optional(), Length(max=255)]
    )
    patient_gender = SelectField(
        'Patient Gender',
        choices=[
            ('', '-- Select --'),
            ('Male', 'Male'),
            ('Female', 'Female'),
            ('Other', 'Other'),
        ],
        validators=[Optional()],
    )
    doctors_name = StringField(
        "Doctor's Name",
        validators=[Optional(), Length(max=255)]
    )
    registration_docket_no = StringField(
        'Registration/Docket No.',
        validators=[Optional(), Length(max=100)]
    )
    ward_clinic = StringField(
        'Ward/Clinic',
        validators=[Optional(), Length(max=255)]
    )
    test_requested = StringField(
        'Test Requested',
        validators=[Optional(), Length(max=500)]
    )
    diagnosis_indicated = TextAreaField(
        'Diagnosis Indicated',
        validators=[Optional()]
    )
    volume = StringField(
        'Volume',
        validators=[Optional(), Length(max=100)]
    )


class PharmaceuticalSampleRegisterForm(SampleRegisterForm):
    """Registration form for Pharmaceutical samples.
    No parish, add Formulation Type, lab_number auto-generated,
    date_received auto-set from date_registered.
    """
    # Remove parish
    parish = None

    date_received = DateField('Date Received', validators=[DataRequired()])

    # lab_number is auto-generated (not required on form)
    lab_number = StringField('Lab Number', validators=[Optional(), Length(max=50)])

    quantity = StringField(
        'No./Quantity of Sample',
        validators=[Optional(), Length(max=100)]
    )
    formulation_type = SelectField(
        'Formulation Type',
        choices=FORMULATION_TYPE_CHOICES,
        validators=[Optional()],
    )
    manufacturer = StringField(
        'Manufacturer',
        validators=[Optional(), Length(max=255)]
    )
    api = StringField(
        'API',
        validators=[Optional(), Length(max=255)]
    )
    lot_number = StringField(
        'Lot Number',
        validators=[Optional(), Length(max=100)]
    )
    expiration_date = DateField(
        'Expiration Date',
        validators=[Optional()]
    )

    def validate_lab_number(self, field):
        """Override: allow empty lab_number (will be auto-generated)."""
        if field.data:
            from app.models import Sample
            existing = Sample.query.filter_by(lab_number=field.data).first()
            if existing:
                raise ValidationError('Lab number already exists.')


class FoodMilkSampleRegisterForm(SampleRegisterForm):
    """Registration form for Food (Milk) samples.
    Uses volume, no parish. Source stored in source field only (fix duplicate).
    """
    # Remove parish
    parish = None

    # Fix: The base SampleRegisterForm has an optional source field.
    # For Milk samples, Source is required and is the primary identifying field.
    # We override it here with DataRequired to enforce this.
    # The base class sample_name remains for the milk sample name.
    source = StringField('Source', validators=[DataRequired(), Length(max=255)])

    # Use volume instead of quantity
    quantity = None
    volume = StringField(
        'Volume',
        validators=[Optional(), Length(max=100)]
    )
    milk_type = SelectField(
        'Milk Type',
        choices=[
            ('R', 'Raw Milk'),
            ('P', 'Processed Milk'),
        ],
        validators=[Optional()]
    )
    lot_number = StringField(
        'Lot Number',
        validators=[Optional(), Length(max=100)]
    )
    expiration_date = DateField(
        'Expiration Date',
        validators=[Optional()]
    )


class FoodAlcoholSampleRegisterForm(SampleRegisterForm):
    """Registration form for Food (Alcohol) samples.
    No parish, no patient name, alcohol type dropdown, Claim/Butt # field,
    Batch/Lot Number field.
    """
    # Remove parish and patient_name
    parish = None
    patient_name = None

    quantity = StringField(
        'No./Quantity of Sample',
        validators=[Optional(), Length(max=100)]
    )
    alcohol_type = SelectField(
        'Alcohol Type',
        choices=[
            ('', '-- Select Type --'),
            ('Alcohol Determination', 'Alcohol Determination'),
            ('Denatured Alcohol (bitrex)', 'Denatured Alcohol (bitrex)'),
            ('Alcohol Determination and Denatured', 'Alcohol Determination and Denatured'),
        ],
        validators=[Optional()]
    )
    claim_butt_number = StringField(
        'Claim/Butt #',
        validators=[Optional(), Length(max=100)]
    )
    batch_lot_number = StringField(
        'Batch / Lot Number',
        validators=[Optional(), Length(max=100)]
    )


def get_sample_register_form(sample_type):
    """
    Factory function to get the appropriate registration form
    based on the sample type (Branch enum).
    
    Args:
        sample_type: Branch enum value or string
        
    Returns:
        Form class appropriate for the sample type
    """
    from app.models import Branch
    
    # Handle string input (convert to Branch enum)
    if isinstance(sample_type, str):
        try:
            sample_type = Branch[sample_type]
        except (KeyError, TypeError):
            return SampleRegisterForm
    
    form_map = {
        Branch.TOXICOLOGY: ToxicologySampleRegisterForm,
        Branch.PHARMACEUTICAL: PharmaceuticalSampleRegisterForm,
        Branch.PHARMACEUTICAL_NR: PharmaceuticalSampleRegisterForm,
        Branch.FOOD_MILK: FoodMilkSampleRegisterForm,
        Branch.FOOD_ALCOHOL: FoodAlcoholSampleRegisterForm,
    }
    
    return form_map.get(sample_type, SampleRegisterForm)


class SampleEditForm(FlaskForm):
    lab_number = StringField('Lab Number', validators=[DataRequired(), Length(max=50)])
    sample_name = StringField('Sample Name', validators=[DataRequired(), Length(max=255)])
    sample_type = SelectField(
        'Laboratory',
        choices=[(b.name, b.value) for b in Branch],
        validators=[DataRequired()],
    )
    description = TextAreaField('Description', validators=[Optional()])
    quantity = StringField('Quantity / Volume', validators=[Optional(), Length(max=100)])
    volume = StringField('Volume', validators=[Optional(), Length(max=100)])
    parish = StringField('Parish', validators=[Optional(), Length(max=100)])
    patient_name = StringField('Patient Name (Toxicology)', validators=[Optional(), Length(max=255)])
    source = StringField('Source', validators=[Optional(), Length(max=255)])
    formulation_type = SelectField(
        'Formulation Type',
        choices=FORMULATION_TYPE_CHOICES,
        validators=[Optional()],
    )
    manufacturer = StringField('Manufacturer', validators=[Optional(), Length(max=255)])
    active_ingredient = SelectMultipleField(
        'Active Ingredient / API',
        choices=[],
        validators=[Optional()],
    )
    api = StringField('API', validators=[Optional(), Length(max=255)])
    alcohol_type = SelectField(
        'Alcohol Type',
        choices=[
            ('', '-- Select Type --'),
            ('Alcohol Determination', 'Alcohol Determination'),
            ('Denatured Alcohol (bitrex)', 'Denatured Alcohol (bitrex)'),
            ('Alcohol Determination and Denatured', 'Alcohol Determination and Denatured'),
        ],
        validators=[Optional()]
    )
    claim_butt_number = StringField('Claim/Butt #', validators=[Optional(), Length(max=100)])
    batch_lot_number = StringField('Batch / Lot Number', validators=[Optional(), Length(max=100)])
    milk_type = SelectField(
        'Milk Type',
        choices=[
            ('', '-- Select Type --'),
            ('R', 'Raw Milk'),
            ('P', 'Processed Milk'),
        ],
        validators=[Optional()]
    )
    lot_number = StringField('Lot Number', validators=[Optional(), Length(max=100)])
    expiration_date = DateField('Expiration Date', validators=[Optional()])
    toxicology_sample_type_name = SelectField(
        'Sample Type',
        choices=TOXICOLOGY_SAMPLE_TYPE_CHOICES,
        validators=[Optional()],
    )
    doctors_name = StringField("Doctor's Name", validators=[Optional(), Length(max=255)])
    registration_docket_no = StringField('Registration/Docket No.', validators=[Optional(), Length(max=100)])
    patient_gender = SelectField(
        'Patient Gender',
        choices=[
            ('', '-- Select --'),
            ('Male', 'Male'),
            ('Female', 'Female'),
            ('Other', 'Other'),
        ],
        validators=[Optional()],
    )
    ward_clinic = StringField('Ward/Clinic', validators=[Optional(), Length(max=255)])
    test_requested = StringField('Test Requested', validators=[Optional(), Length(max=500)])
    diagnosis_indicated = TextAreaField('Diagnosis Indicated', validators=[Optional()])
    expected_report_date = DateField('Expected Report Date', validators=[Optional()])
    scanned_file = FileField(
        'Replace Submission Form',
        validators=[FileAllowed(
            ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'doc', 'docx'],
            'Only PDF, image, and Word document files allowed.'
        )],
    )
    accreditation = SelectField(
        'Accreditation Status',
        choices=[
            ('', '-- Select Accreditation --'),
            ('accredited', 'Accredited'),
            ('not_accredited', 'Not Accredited'),
        ],
        validators=[Optional()],
    )
    submit = SubmitField('Update Sample')


class CheckboxSelectMultiple(widgets.ListWidget):
    def __call__(self, field, **kwargs):
        kwargs.setdefault('id', field.id)
        html = ['<div class="row">']
        for val, label, selected, render_kw in field.iter_choices():
            html.append(
                f'<div class="col-md-6 mb-2">'
                f'<div class="form-check">'
                f'<input class="form-check-input" type="checkbox" '
                f'name="{field.name}" value="{val}" '
                f'id="{field.id}-{val}" '
                f'{"checked" if selected else ""}>'
                f'<label class="form-check-label" for="{field.id}-{val}">'
                f'{label}</label></div></div>'
            )
        html.append('</div>')
        return Markup(''.join(html))


class SampleAssignForm(FlaskForm):
    chemist_ids = SelectMultipleField(
        'Assign to Chemist(s)',
        coerce=int,
        widget=CheckboxSelectMultiple(),
        validators=[DataRequired(message='Select at least one chemist.')],
    )
    # For sample types with predefined test names, use multi-select dropdown
    test_names = SelectMultipleField(
        'Test Name(s)',
        choices=[],
        validators=[Optional()],
    )
    # Fallback free-text field for sample types without predefined tests
    test_name = StringField('Test Name', validators=[Optional(), Length(max=255)])
    # Multi-select test references
    test_reference_select = SelectMultipleField(
        'Test Reference(s)',
        choices=[],
        validators=[Optional()],
    )
    # Fallback free-text reference
    test_reference = StringField('Test Reference', validators=[Optional(), Length(max=255)])
    expected_completion = DateField('Expected Completion Date', validators=[Optional()])
    comments = TextAreaField('Comments', validators=[Optional()])
    quantity_volume = StringField('Quantity / Volume', validators=[Optional(), Length(max=100)])
    # Feature 4 – OOS Investigation
    oos_investigation = BooleanField(
        'OOS – Out of Specification Investigation',
        default=False,
    )
    submit = SubmitField('Assign Sample')

    # Validation removed to allow flexible scheduling: future dates for
    # forward-scheduling and back-dated corrections via BackDateRequest
    # approval workflow


# ---------------------------------------------------------------------------
# Report forms
# ---------------------------------------------------------------------------

class ReportSubmitForm(FlaskForm):
    report_text = TextAreaField('Report / Findings', validators=[Optional()])
    report_file = FileField(
        'Attach Report File',
        validators=[
            Optional(),
            FileAllowed(
                ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'doc', 'docx'],
                'Only PDF, image, and Word document files allowed.'
            ),
        ],
    )
    test_date = DateField('Test Date', validators=[Optional()])
    meets_specifications = SelectField(
        'Meets Specifications?',
        choices=[
            ('', '-- Select --'),
            ('Yes', 'Yes'),
            ('No', 'No'),
            ('N/A', 'N/A'),
        ],
        validators=[Optional()],
    )
    report_comments = TextAreaField('Additional Comments', validators=[Optional()])
    all_samples_returned = SelectField(
        'All Samples Returned?',
        choices=[
            ('', '-- Select --'),
            ('Yes', 'Yes'),
            ('No', 'No'),
        ],
        validators=[Optional()],
    )
    return_quantity = StringField('Quantity Returned', validators=[Optional(), Length(max=100)])
    submit = SubmitField('Submit Report')


class PreliminaryReviewForm(FlaskForm):
    """Preliminary administrative / completeness review by Officer.
    Each checklist item uses Yes/No/N/A (tri-state) with visual indicators:
    Yes → Green, No → Red, N/A → Yellow.
    If any item is set to 'No', progression is blocked (return only).
    """

    # -- Corrections --
    chk_original_entry_visible = RadioField(
        'Original entry remains visible',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )

    # -- Signatures and Review --
    chk_entries_signed = RadioField(
        'Entries signed/initialed by analyst',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_date_recorded = RadioField(
        'Date of entry recorded',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_conclusions_signed_dated = RadioField(
        'Conclusions signed and dated',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_report_signed_dated = RadioField(
        'Report signed and dated',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )

    # -- Attachments --
    chk_printouts_attached = RadioField(
        'Printouts/graphs attached securely',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_attachments_labeled = RadioField(
        'Attachments labeled and dated',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_analyst_initials = RadioField(
        'Analyst initials across attachment',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_templates_completed = RadioField(
        'Templates completed',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )

    # -- General Documentation --
    chk_writing_legible = RadioField(
        'Writing clear and legible',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_logbooks_updated = RadioField(
        'Logbooks updated',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_toc_updated = RadioField(
        'Table of contents updated',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )
    chk_pages_numbered = RadioField(
        'Pages numbered',
        choices=[('yes', 'Yes'), ('no', 'No'), ('na', 'N/A')],
        default='na', validators=[DataRequired()]
    )

    action = SelectField(
        'Decision',
        choices=[
            ('', '-- Select Action --'),
            ('approved', 'Approve – Forward to Senior Chemist'),
            ('returned', 'Return for Correction'),
            ('not_accepted', 'Not Accepted – Reject Report'),
        ],
        validators=[DataRequired()],
    )
    review_comments = TextAreaField('Comments', validators=[Optional()])
    return_scope = SelectField(
        'Return Scope',
        choices=[
            ('single', 'Return selected report only'),
            ('all', 'Return all reports for all assigned tests'),
        ],
        default='single',
        validators=[Optional()],
    )
    submit = SubmitField('Submit Review')

    # Ordered checklist items grouped by category for template rendering
    CHECKLIST_CATEGORIES = [
        ('Corrections', [
            'chk_original_entry_visible',
        ]),
        ('Signatures and Review', [
            'chk_entries_signed',
            'chk_date_recorded',
            'chk_conclusions_signed_dated',
            'chk_report_signed_dated',
        ]),
        ('Attachments', [
            'chk_printouts_attached',
            'chk_attachments_labeled',
            'chk_analyst_initials',
            'chk_templates_completed',
        ]),
        ('General Documentation', [
            'chk_writing_legible',
            'chk_logbooks_updated',
            'chk_toc_updated',
            'chk_pages_numbered',
        ]),
    ]

    def has_any_no(self):
        """Check if any checklist item is set to 'No'."""
        for _, fields in self.CHECKLIST_CATEGORIES:
            for field_name in fields:
                if getattr(self, field_name).data == 'no':
                    return True
        return False


class ReportReviewForm(FlaskForm):
    """Senior Chemist Review."""
    action = SelectField(
        'Decision',
        choices=[
            ('', '-- Select Action --'),
            ('accepted', 'Accept Report'),
            ('returned', 'Return for Correction'),
        ],
        validators=[DataRequired()],
    )
    out_of_spec = BooleanField('Mark as Out of Specification')
    reassign_chemist_id = SelectField(
        'Reassign to Different Chemist',
        choices=[],
        coerce=int,
        validators=[Optional()],
    )
    review_comments = TextAreaField('Comments', validators=[Optional()])
    return_scope = SelectField(
        'Return Scope',
        choices=[
            ('single', 'Return selected report only'),
            ('all', 'Return all reports for all assigned tests'),
        ],
        default='single',
        validators=[Optional()],
    )
    submit = SubmitField('Submit Review')


class SubmitToDeputyForm(FlaskForm):
    """Senior Chemist submits report package to Deputy Government Chemist."""
    summary_report = TextAreaField(
        'Summary Report (required for Pharmaceutical samples)',
        validators=[Optional()],
    )
    summary_report_file = FileField(
        'Attach Summary Report File',
        validators=[FileAllowed(
            ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'doc', 'docx'],
            'Only PDF, image, and Word document files allowed.'
        )],
    )
    submit = SubmitField('Submit to Deputy Government Chemist')


class DeputyReviewForm(FlaskForm):
    """Deputy Government Chemist reviews submission."""
    action = SelectField(
        'Decision',
        choices=[
            ('', '-- Select Action --'),
            ('approved', 'Accept Report – Proceed to Certificate'),
            ('returned', 'Return to Senior Chemist'),
        ],
        validators=[DataRequired()],
    )
    review_comments = TextAreaField('Comments', validators=[Optional()])
    submit = SubmitField('Submit Review')


class CertificateForm(FlaskForm):
    """Deputy Government Chemist prepares Certificate of Analysis."""
    accreditation = SelectField(
        'Accreditation Status',
        choices=[
            ('', '-- Select Accreditation --'),
            ('accredited', 'Accredited'),
            ('not_accredited', 'Not Accredited'),
        ],
        validators=[Optional()],
    )
    certificate_text = TextAreaField(
        'Certificate of Analysis', validators=[DataRequired()]
    )
    certificate_file = FileField(
        'Attach Certificate File',
        validators=[FileAllowed(
            ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'doc', 'docx'],
            'Only PDF, image, and Word document files allowed.'
        )],
    )
    coa_reference = StringField(
        'COA Reference Number',
        validators=[Optional(), Length(max=255)]
    )
    submit = SubmitField('Submit Certificate for HOD Review')


class HODReviewForm(FlaskForm):
    """Government Chemist (HOD) reviews and signs Certificate of Analysis."""
    action = SelectField(
        'Decision',
        choices=[
            ('', '-- Select Action --'),
            ('sign', 'Sign Certificate – Complete Process'),
            ('returned', 'Return to Deputy for Correction'),
        ],
        validators=[DataRequired()],
    )
    review_comments = TextAreaField('Comments', validators=[Optional()])
    submit = SubmitField('Submit Decision')


class SupportingDocumentForm(FlaskForm):
    """Form for uploading additional supporting documents."""
    file = FileField(
        'Supporting Document',
        validators=[
            DataRequired(message='Please select a file to upload.'),
            FileAllowed(
                ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'doc', 'docx'],
                'Only PDF, image, and Word document files allowed.'
            ),
        ],
    )
    description = StringField('Description', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Upload Document')


class SampleImageForm(FlaskForm):
    """Form for uploading image(s) of a sample (previewed on detail page)."""
    image = FileField(
        'Sample Image',
        validators=[
            DataRequired(message='Please select an image to upload.'),
            FileAllowed(
                ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff'],
                'Only image files (PNG, JPG, JPEG, GIF, BMP, TIFF) are allowed.'
            ),
        ],
    )
    caption = StringField('Caption', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Upload Image')


class NonWorkingDayForm(FlaskForm):
    """Form for managing non-working days (holidays, emergency closures)."""
    date = DateField('Date', validators=[DataRequired()])
    description = StringField('Description', validators=[DataRequired(), Length(max=255)])
    day_type = SelectField(
        'Type',
        choices=[
            ('holiday', 'Public Holiday'),
            ('emergency', 'Emergency / Disaster Closure'),
        ],
        validators=[DataRequired()],
    )
    submit = SubmitField('Add Non-Working Day')


# ---------------------------------------------------------------------------
# Back-Date Request
# ---------------------------------------------------------------------------

class BackDateRequestForm(FlaskForm):
    """Request to back-date any date field on a sample or assignment."""
    field_name = SelectField(
        'Date Field',
        choices=[
            ('date_registered', 'Date Registered'),
            ('date_received', 'Date Received'),
            ('expected_report_date', 'Expected Report Date'),
            ('assigned_date', 'Assigned Date'),
            ('expected_completion', 'Expected Completion Date'),
            ('report_submitted_at', 'Report Submitted Date'),
            ('test_date', 'Test Date'),
            ('reviewed_at', 'Senior Chemist Review Date'),
            ('deputy_reviewed_at', 'Deputy Government Chemist Review Date'),
            ('certificate_prepared_at', 'Certificate Reissue Date'),
            ('certified_at', 'Certificate Signed Date'),
        ],
        validators=[DataRequired(message='Please select a date field.')],
    )
    assignment_id = SelectField(
        'Assignment (if applicable)',
        choices=[],
        coerce=int,
        validators=[Optional()],
    )
    proposed_date = DateField(
        'Proposed Date',
        validators=[DataRequired(message='A proposed date is required.')],
    )
    reason = TextAreaField(
        'Reason for Back-Dating',
        validators=[DataRequired(message='Please provide a reason for the request.'),
                    Length(max=1000)],
    )
    submit = SubmitField('Submit Back-Date Request')


# ---------------------------------------------------------------------------
# Delete Request
# ---------------------------------------------------------------------------

class DeleteRequestForm(FlaskForm):
    """Request to delete a sample or assignment (requires HOD approval)."""
    reason = TextAreaField(
        'Reason for Deletion',
        validators=[DataRequired(message='Please provide a reason for the deletion request.'),
                    Length(max=1000)],
    )
    submit = SubmitField('Submit Deletion Request')


# ---------------------------------------------------------------------------
# COA Decertify / Re-Issue (Feature 5)
# ---------------------------------------------------------------------------

class COADecertifyForm(FlaskForm):
    """HOD / Government Chemist decertifies a signed COA."""
    reason = TextAreaField(
        'Reason for Decertification',
        validators=[DataRequired(message='Please provide a reason.'), Length(max=1000)],
    )
    submit = SubmitField('Decertify COA')


class COAReissueForm(FlaskForm):
    """Re-issue an updated Certificate of Analysis."""
    certificate_text = TextAreaField('Updated Certificate of Analysis', validators=[DataRequired()])
    certificate_file = FileField(
        'Attach Updated Certificate File',
        validators=[FileAllowed(
            ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'doc', 'docx'],
            'Only PDF, image, and Word document files allowed.'
        )],
    )
    coa_reference = StringField('COA Reference Number', validators=[Optional(), Length(max=255)])
    submit = SubmitField('Re-Issue Certificate')


class MarkOutOfSpecForm(FlaskForm):
    """HOD / Deputy / Senior Chemist marks a certified sample as out of specification."""
    reason = TextAreaField(
        'Reason for Out-of-Spec Determination',
        validators=[DataRequired(message='Please provide a reason.'), Length(max=1000)],
    )
    submit = SubmitField('Mark as Out of Spec')


# ---------------------------------------------------------------------------
# Invoice (Feature 9)
# ---------------------------------------------------------------------------

# Test types for invoice
INVOICE_TEST_TYPE_CHOICES = [
    ('', '-- Select Type --'),
    ('Pharmaceutical', 'Pharmaceutical'),
    ('Food (Milk)', 'Food (Milk)'),
    ('Food (Alcohol)', 'Food (Alcohol)'),
    ('Toxicology', 'Toxicology'),
]

# Pharma test price choices (test name → cost) for the invoice dropdown
from app.models import PHARMA_TEST_PRICES

INVOICE_PHARMA_TEST_CHOICES = (
    [('', '-- Select Test --')]
    + [(name, f'{name} (${cost:,})') for name, cost in sorted(PHARMA_TEST_PRICES.items())]
)


class InvoiceItemForm(FlaskForm):
    """A single invoice line item sub-form."""
    class Meta:
        csrf = False

    test_name = StringField('Test Name', validators=[DataRequired(), Length(max=255)])
    test_type = SelectField('Type of Test', choices=INVOICE_TEST_TYPE_CHOICES, validators=[Optional()])
    unit_cost = StringField('Unit Cost', validators=[DataRequired()])
    quantity = IntegerField('Quantity', validators=[DataRequired(), NumberRange(min=1)], default=1)


class InvoiceCreateForm(FlaskForm):
    """Create an invoice for a sample."""
    notes = TextAreaField('Notes', validators=[Optional()])
    submit = SubmitField('Create Invoice')


# ---------------------------------------------------------------------------
# Dropdown Configuration (Feature 11)
# ---------------------------------------------------------------------------

DROPDOWN_CATEGORY_CHOICES = _sort_choices([
    ('', '-- Select Category --'),
    ('api', 'API (Active Pharmaceutical Ingredient)'),
    ('test_name', 'Test Names'),
    ('test_type', 'Test Types'),
    ('invoice_test', 'Invoice Test Items'),
    ('formulation_type', 'Formulation Types'),
    ('toxicology_sample_type', 'Toxicology Sample Types'),
])


class DropdownConfigForm(FlaskForm):
    """Add or edit a dropdown configuration entry."""
    category = SelectField(
        'Category',
        choices=DROPDOWN_CATEGORY_CHOICES,
        validators=[DataRequired()],
    )
    value = StringField('Value', validators=[DataRequired(), Length(max=255)])
    label = StringField('Display Label', validators=[Optional(), Length(max=255)])
    branch = SelectField(
        'Branch (Laboratory)',
        choices=[('', '-- All Branches --')] + [(b.value, b.value) for b in Branch],
        validators=[Optional()],
    )
    sort_order = IntegerField('Sort Order', validators=[Optional()], default=0)
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save')


class DropdownBulkAddForm(FlaskForm):
    """Bulk-add multiple dropdown entries for a single category."""
    category = SelectField(
        'Category',
        choices=DROPDOWN_CATEGORY_CHOICES,
        validators=[DataRequired()],
    )
    branch = SelectField(
        'Branch (Laboratory)',
        choices=[('', '-- All Branches --')] + [(b.value, b.value) for b in Branch],
        validators=[Optional()],
    )
    bulk_values = TextAreaField(
        'Values (one per line)',
        validators=[DataRequired()],
        description=(
            'Enter one value per line. '
            'Optionally separate value and display label with a pipe: '
            'value | Display Label'
        ),
    )
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Add All')
