import json
import re
import base64
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
import os
import threading
import time
import requests
from supabase import Client, create_client
from flask import Flask, request, render_template, flash, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from functools import wraps

app = Flask(__name__)
app.secret_key = "124"  # ← change this in production!

app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres.hmhztencjtycadsmodif:V9syIHsOdN015qNf@aws-1-eu-west-1.pooler.supabase.com:5432/postgres'
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

SUPABASE_URL = "https://hmhztencjtycadsmodif.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhtaHp0ZW5janR5Y2Fkc21vZGlmIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MjQ2MTQ5MCwiZXhwIjoyMDg4MDM3NDkwfQ.J-VrGQRoc7hvhG--8oI9asKa6hUl8ME-lihjjF--cpE"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ADMIN_PASSWORD = "admin"
db = SQLAlchemy(app)

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'ppt', 'pptx'}

# ── KEEP-ALIVE (prevents Render free tier from sleeping) ──────────────────
APP_URL = "https://unex.onrender.com"  # ← replace with your Render URL when deployed

def keep_alive():
    while True:
        time.sleep(840)  # ping every 14 minutes (Render sleeps after 15)
        try:
            requests.get(APP_URL)
        except Exception:
            pass

threading.Thread(target=keep_alive, daemon=True).start()
# ─────────────────────────────────────────────────────────────────────────


# ── MODELS ────────────────────────────────────────────────────────────────

class Notes(db.Model):
    __tablename__ = 'notes'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_code    = db.Column(db.String(200), nullable=False)
    course_title   = db.Column(db.String(200))
    department     = db.Column(db.String(200), nullable=False)
    level          = db.Column(db.String(200), nullable=False)
    lecturer_name  = db.Column(db.String(200), nullable=False)
    weeks          = db.Column(db.String(200), nullable=False)
    description    = db.Column(db.String(500), nullable=True)
    academic_year  = db.Column(db.String(20), nullable=False)
    semester       = db.Column(db.String(20), nullable=False)           # ← NEW: 1st Semester / 2nd Semester
    file_url       = db.Column(db.Text, nullable=False)
    downloadable   = db.Column(db.Boolean, default=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Notes {self.course_code} - {self.course_title} ({self.academic_year} {self.semester})>"


class CourseCode(db.Model):
    __tablename__ = 'added_course_code'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Department(db.Model):
    __tablename__ = 'departments'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Department {self.name}>"


# Create tables (only runs once)
with app.app_context():
    db.create_all()

    # Insert sample department if table is empty
    if Department.query.count() == 0:
        supabase.table('departments').insert({
            "name": "computer engineering",
            "created_at": "now()"
        }).execute()


# ── DECORATORS ────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash("Please log in as admin", "error")
            return redirect(url_for('admin_login_page'))
        return f(*args, **kwargs)
    return decorated_function


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ── ROUTES ────────────────────────────────────────────────────────────────

@app.route("/admin/validate", methods=["POST"])
def admin_validate():
    password = request.form.get('password')
    if not password:
        flash('password required', 'error')
        return redirect(url_for('admin_login_page'))

    if password == ADMIN_PASSWORD:
        session["admin_logged_in"] = True
        flash('successfully logged in', 'success')
        return redirect(url_for('admin_dashboard_page'))

    flash('Incorrect Password', 'error')
    return redirect(url_for('admin_login_page'))


@app.route('/api/dropdown-data', methods=['GET'])
@admin_required
def get_dropdown_data():
    try:
        course_res = supabase.table('added_course_code')\
            .select('code').order('code').execute()
        course_codes = [row['code'] for row in course_res.data] if course_res.data else []

        dept_res = supabase.table('departments')\
            .select('name').order('name').execute()
        departments = [row['name'] for row in dept_res.data] if dept_res.data else []

        return jsonify({
            'course_codes': course_codes,
            'departments': departments
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/notes', methods=['GET', 'POST'])
@admin_required
def add_notes():
    if request.method == 'POST':
        course_code     = request.form.get('course_code', '').strip().upper()
        academic_year   = request.form.get('academic_year', '').strip()
        semester        = request.form.get('semester', '').strip()                     # ← NEW
        department      = request.form.get('department', '').strip()
        level           = request.form.get('level', '').strip()
        lecturer_name   = request.form.get('lecturer_name', '').strip()
        weeks           = request.form.get('weeks', '').strip()
        description     = request.form.get('description', '').strip()
        downloadable    = 'downloadable' in request.form

        required = [course_code, academic_year, semester, department, level, lecturer_name]
        if not all(required):
            flash('Please fill all required fields (course code, academic year, semester, department, level, lecturer)', 'error')
            return redirect(url_for('add_notes'))

        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            flash('No valid files selected', 'error')
            return redirect(url_for('add_notes'))

        success_count = 0
        errors = []

        for file in files:
            if not file or not allowed_file(file.filename):
                errors.append(f"Skipped invalid file: {file.filename}")
                continue

            try:
                filename = secure_filename(file.filename)
                timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
                file_path = f"notes/{course_code}/{timestamp}_{filename}"

                file_content = file.read()
                if not file_content:
                    errors.append(f"Empty file: {file.filename}")
                    continue

                supabase.storage.from_('notes').upload(
                    path=file_path,
                    file=file_content,
                    file_options={"content-type": file.content_type or "application/octet-stream"}
                )

                public_url = supabase.storage.from_('notes').get_public_url(file_path)

                supabase.table('notes').insert({
                    "course_code": course_code,
                    "course_title": course_code,
                    "department": department,
                    "level": level,
                    "lecturer_name": lecturer_name,
                    "weeks": weeks or "Not specified",
                    "description": description or "No description provided",
                    "academic_year": academic_year,
                    "semester": semester,                                           # ← NEW
                    "file_url": public_url,
                    "downloadable": downloadable,
                    "created_at": "now()",
                    "updated_at": "now()"
                }).execute()

                success_count += 1

            except Exception as e:
                errors.append(f"Failed {file.filename}: {str(e)}")

        if success_count > 0:
            flash(f"Successfully uploaded {success_count} file(s)", 'success')
        if errors:
            for msg in errors:
                flash(msg, 'error')

        return redirect(url_for('add_notes'))

    try:
        course_res = supabase.table('added_course_code').select('code').order('code').execute()
        course_codes = [r['code'] for r in course_res.data] if course_res.data else []

        dept_res = supabase.table('departments').select('name').order('name').execute()
        departments = [r['name'] for r in dept_res.data] if dept_res.data else []
    except Exception as e:
        flash(f"Could not load dropdown options: {str(e)}", 'error')
        course_codes = []
        departments = []

    return render_template(
        'admin_bulk_upload.html',
        course_codes=course_codes,
        departments=departments
    )


@app.route('/admin/register_codes', methods=["GET", "POST"])
@admin_required
def register_code():
    if request.method == "POST":
        action = request.form.get('action', 'add')

        if action == 'delete':
            code_to_delete = request.form.get('code', '').strip().upper()
            if not code_to_delete:
                flash('No code selected for deletion', 'error')
            else:
                try:
                    supabase.table('added_course_code').delete().eq('code', code_to_delete).execute()
                    flash(f"Deleted '{code_to_delete}' successfully", 'success')
                except Exception as e:
                    flash(f"Error deleting '{code_to_delete}': {str(e)}", 'error')
            return redirect(url_for('register_code'))

        code_input = request.form.get('course_name', '').strip().upper()
        if not code_input:
            flash('Course code cannot be empty', 'error')
            return redirect(url_for('register_code'))

        try:
            check = supabase.table('added_course_code').select('code').eq('code', code_input).execute()
            if check.data:
                flash(f"'{code_input}' already exists", 'error')
                return redirect(url_for('register_code'))

            supabase.table('added_course_code').insert({
                "code": code_input,
                "created_at": "now()"
            }).execute()
            flash(f"Added '{code_input}' successfully", 'success')
        except Exception as e:
            flash(f"Error adding code: {str(e)}", 'error')

        return redirect(url_for('register_code'))

    try:
        res = supabase.table('added_course_code').select('code, created_at').order('code').execute()
        existing_codes = res.data or []
    except Exception:
        existing_codes = []
        flash("Could not load course codes", 'error')

    return render_template('admin_course_code.html', existing_codes=existing_codes)


@app.route('/admin/edit', methods=['GET', 'POST'])
@admin_required
def edit_notes():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'delete':
            note_id = request.form.get('note_id')
            if note_id:
                try:
                    supabase.table('notes').delete().eq('id', note_id).execute()
                    flash("Note deleted successfully", 'success')
                except Exception as e:
                    flash(f"Error deleting note: {str(e)}", 'error')
            else:
                flash("No note ID provided", 'error')
            return redirect(url_for('edit_notes'))

        note_id = request.form.get('note_id')
        if not note_id:
            flash("No note ID provided for update", 'error')
            return redirect(url_for('edit_notes'))

        try:
            update_data = {
                "course_code": request.form.get('course_code', '').strip().upper(),
                "course_title": request.form.get('course_title', '').strip(),
                "department": request.form.get('department', '').strip(),
                "level": request.form.get('level', '').strip(),
                "lecturer_name": request.form.get('lecturer_name', '').strip(),
                "weeks": request.form.get('weeks', '').strip(),
                "description": request.form.get('description', '').strip(),
                "academic_year": request.form.get('academic_year', '').strip(),
                "semester": request.form.get('semester', '').strip(),               # ← NEW
                "downloadable": 'downloadable' in request.form,
                "updated_at": "now()"
            }

            required = ['course_code', 'course_title', 'department', 'level', 'lecturer_name', 'academic_year', 'semester']
            if any(not update_data.get(k) for k in required):
                flash("Please fill all required fields", 'error')
                return redirect(url_for('edit_notes'))

            supabase.table('notes').update(update_data).eq('id', note_id).execute()
            flash("Note updated successfully", 'success')

        except Exception as e:
            flash(f"Error updating note: {str(e)}", 'error')

        return redirect(url_for('edit_notes'))

    search_query = request.args.get('search', '').strip().lower()

    try:
        query = supabase.table('notes').select('*')

        if search_query:
            query = query.or_(
                f"course_code.ilike.%{search_query}%,"
                f"course_title.ilike.%{search_query}%,"
                f"lecturer_name.ilike.%{search_query}%,"
                f"department.ilike.%{search_query}%,"
                f"academic_year.ilike.%{search_query}%,"
                f"semester.ilike.%{search_query}%"                                 # ← NEW
            )

        notes_res = query.order('created_at', desc=True).execute()
        notes_list = notes_res.data or []

        for note in notes_list:
            if note.get('created_at'):
                note['created_at_dt'] = datetime.fromisoformat(note['created_at'].replace('Z', '+00:00'))
            if note.get('updated_at'):
                note['updated_at_dt'] = datetime.fromisoformat(note['updated_at'].replace('Z', '+00:00'))

    except Exception as e:
        flash(f"Could not load notes: {str(e)}", 'error')
        notes_list = []

    try:
        dept_res = supabase.table('departments').select('name').order('name').execute()
        departments = [r['name'] for r in dept_res.data] if dept_res.data else []
    except Exception:
        departments = []

    return render_template(
        'admin_edit_notes.html',
        notes_list=notes_list,
        departments=departments,
        search_query=search_query
    )


@app.route('/admin/delete-note', methods=['POST'])
@admin_required
def delete_note_ajax():
    note_id = request.json.get('note_id')
    if not note_id:
        return jsonify({'error': 'No note ID'}), 400

    try:
        supabase.table('notes').delete().eq('id', note_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/user/notes', methods=['GET'])
def user_notes():
    search_text   = request.args.get('search', '').strip()
    course_code   = request.args.get('course_code', '').strip().upper()
    department    = request.args.get('department', '').strip()
    level         = request.args.get('level', '').strip()
    lecturer      = request.args.get('lecturer', '').strip()
    academic_year = request.args.get('academic_year', '').strip()
    semester      = request.args.get('semester', '').strip()                   # ← NEW

    try:
        query = supabase.table('notes').select('*')

        if search_text:
            query = query.or_(
                f"course_title.ilike.%{search_text}%,"
                f"description.ilike.%{search_text}%"
            )

        if course_code:
            query = query.eq('course_code', course_code)
        if department:
            query = query.eq('department', department)
        if level:
            query = query.eq('level', level)
        if lecturer:
            query = query.ilike('lecturer_name', f'%{lecturer}%')
        if academic_year:
            query = query.eq('academic_year', academic_year)
        if semester:                                                                # ← NEW
            query = query.eq('semester', semester)

        notes_res = query.order('created_at', desc=True).execute()
        notes_list = notes_res.data or []

    except Exception as e:
        flash(f"Error loading notes: {str(e)}", 'error')
        notes_list = []

    try:
        dept_res = supabase.table('departments').select('name').order('name').execute()
        departments = [r['name'] for r in dept_res.data] if dept_res.data else []
    except Exception:
        departments = []

    return render_template(
        'user_notes.html',
        notes_list=notes_list,
        departments=departments,
        current_filters={
            'search': search_text,
            'course_code': course_code,
            'department': department,
            'level': level,
            'lecturer': lecturer,
            'academic_year': academic_year,
            'semester': semester                                                   # ← NEW
        }
    )


@app.route('/user/view-note/<int:note_id>', methods=['GET'])
def view_note(note_id):
    try:
        note = supabase.table('notes').select('*').eq('id', note_id).single().execute().data
        if not note:
            flash("Note not found", 'error')
            return redirect(url_for('user_notes'))
    except Exception as e:
        flash(f"Error loading note: {str(e)}", 'error')
        return redirect(url_for('user_notes'))

    return render_template('view_note.html', note=note)


# ── Static / utility pages ────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET'])
def admin_login_page():
    return render_template('admin_login.html')


@app.route('/admin/add', methods=['GET'])
@admin_required
def admin_add_notes_page():
    return redirect(url_for('add_notes'))


@app.route('/', methods=['GET'])
def home():
    return redirect(url_for('user_home'))


@app.route('/user/home', methods=['GET'])
def user_home():
    return render_template('user_home.html')


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard_page():
    return render_template('admin_dashboard.html')


@app.route('/admin/logout', methods=['GET', 'POST'])
@admin_required
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('You have been logged out successfully', 'success')
    return redirect(url_for('admin_login_page'))

@app.route('/user/sponsors')
def sponsors_page():
    return render_template('sponsors.html')


#----------new part -------------
# ── STUDENT DECORATOR  (mirrors admin_required) ─────────────────────
def student_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('student_logged_in'):
            flash("Please log in to continue", "error")
            return redirect(url_for('user_login_page'))
        return f(*args, **kwargs)
    return decorated_function
 
 
# ── SIGNUP ──────────────────────────────────────────────────────────
@app.route('/user/signup', methods=['GET'])
def user_signup_page():
    if session.get('student_logged_in'):
        return redirect(url_for('user_dashboard'))
    try:
        dept_res    = supabase.table('departments').select('name').order('name').execute()
        departments = [r['name'] for r in dept_res.data] if dept_res.data else []
    except Exception:
        departments = []
    return render_template('user_signup.html', departments=departments)
 
 
@app.route('/user/signup', methods=['POST'])
def user_signup():
    full_name     = request.form.get('full_name', '').strip()
    email         = request.form.get('email', '').strip().lower()
    matric_number = request.form.get('matric_number', '').strip().upper()
    department    = request.form.get('department', '').strip()
    level         = request.form.get('level', '').strip()
    password      = request.form.get('password', '')
    confirm_pw    = request.form.get('confirm_password', '')
 
    # ── server-side validation ───────────────────────────────────────
    errors = []
 
    if not full_name:
        errors.append("Full name is required.")
 
    # email: must contain @ and a dot after it — accepts gmail, yahoo, etc.
    email_ok = bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email))
    if not email or not email_ok:
        errors.append("Enter a valid email address (e.g. name@gmail.com).")
 
    # matric: letters, digits, slashes and hyphens — e.g. CSC/2021/001 or MU/EN/0323
    if not matric_number:
        errors.append("Matric number is required.")
    elif not re.match(r'^[A-Z0-9][A-Z0-9/\-]{2,19}$', matric_number):
        errors.append("Matric number format is invalid (e.g. CSC/2021/001 or MU/EN/0323).")
 
    if not department:
        errors.append("Please select your department.")
 
    valid_levels = ['100', '200', '300', '400']
    if not level:
        errors.append("Please select your level.")
    elif level not in valid_levels:
        errors.append("Level must be 100, 200, 300, or 400.")
 
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password != confirm_pw:
        errors.append("Passwords do not match.")
 
    if errors:
        for msg in errors:
            flash(msg, 'error')
        return redirect(url_for('user_signup_page'))
 
    # ── check duplicates ─────────────────────────────────────────────
    try:
        email_check = supabase.table('users').select('id').eq('email', email).execute()
        if email_check.data:
            flash("An account with that email already exists.", 'error')
            return redirect(url_for('user_signup_page'))
 
        matric_check = supabase.table('users').select('id').eq('matric_number', matric_number).execute()
        if matric_check.data:
            flash("That matric number is already registered.", 'error')
            return redirect(url_for('user_signup_page'))
 
    except Exception as e:
        flash(f"Could not verify account details: {str(e)}", 'error')
        return redirect(url_for('user_signup_page'))
 
    # ── create user ──────────────────────────────────────────────────
    try:
        password_hash = generate_password_hash(password)
 
        result = supabase.table('users').insert({
            "full_name":     full_name,
            "email":         email,
            "matric_number": matric_number,
            "department":    department,
            "level":         level,
            "password_hash": password_hash,
            "is_active":     True,
            "created_at":    "now()",
            "updated_at":    "now()"
        }).execute()
 
        # log the student in immediately after signup
        user = result.data[0]
        session['student_logged_in'] = True
        session['student_id']        = user['id']
        session['student_name']      = user['full_name']
        session['student_email']     = user['email']
        session['student_dept']      = user['department']
        session['student_level']     = user['level']
        session['student_matric']    = user['matric_number']
 
        flash(f"Welcome to U-NEX, {full_name.split()[0]}! Your account is ready.", 'success')
        return redirect(url_for('user_dashboard'))
 
    except Exception as e:
        flash(f"Could not create account: {str(e)}", 'error')
        return redirect(url_for('user_signup_page'))
 
 
# ── LOGIN ────────────────────────────────────────────────────────────
@app.route('/user/login', methods=['GET'])
def user_login_page():
    if session.get('student_logged_in'):
        return redirect(url_for('user_dashboard'))
    return render_template('user_login.html')
 
 
@app.route('/user/login', methods=['POST'])
def user_login():
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
 
    if not email or not password:
        flash("Email and password are required.", 'error')
        return redirect(url_for('user_login_page'))
 
    try:
        result = supabase.table('users').select('*').eq('email', email).execute()
 
        if not result.data:
            flash("No account found with that email.", 'error')
            return redirect(url_for('user_login_page'))
 
        user = result.data[0]
 
        if not check_password_hash(user['password_hash'], password):
            flash("Incorrect password. Please try again.", 'error')
            return redirect(url_for('user_login_page'))
 
        if not user.get('is_active', True):
            flash("Your account has been deactivated. Contact support.", 'error')
            return redirect(url_for('user_login_page'))
 
        # set session
        session['student_logged_in'] = True
        session['student_id']        = user['id']
        session['student_name']      = user['full_name']
        session['student_email']     = user['email']
        session['student_dept']      = user['department']
        session['student_level']     = user['level']
        session['student_matric']    = user['matric_number']
 
        flash(f"Welcome back, {user['full_name'].split()[0]}!", 'success')
        return redirect(url_for('user_dashboard'))
 
    except Exception as e:
        flash(f"Login error: {str(e)}", 'error')
        return redirect(url_for('user_login_page'))
 
 
# ── LOGOUT ───────────────────────────────────────────────────────────
@app.route('/user/logout', methods=['GET', 'POST'])
@student_required
def user_logout():
    session.pop('student_logged_in', None)
    session.pop('student_id',        None)
    session.pop('student_name',      None)
    session.pop('student_email',     None)
    session.pop('student_dept',      None)
    session.pop('student_level',     None)
    session.pop('student_matric',    None)
    flash("You've been logged out.", 'success')
    return redirect(url_for('user_login_page'))
 
 
# ── DASHBOARD ───────────────────────────────────────────────────────
@app.route('/user/dashboard')
@student_required
def user_dashboard():
    from datetime import datetime as _dt
 
    # time-based greeting
    hour = _dt.now().hour
    if hour < 12:
        greeting = 'morning'
    elif hour < 17:
        greeting = 'afternoon'
    else:
        greeting = 'evening'
 
    # fetch notes count for the student's dept/level from DB
    try:
        notes_res = supabase.table('notes') \
            .select('id', count='exact') \
            .eq('department', session.get('student_dept', '')) \
            .eq('level', session.get('student_level', '')) \
            .execute()
        notes_count = notes_res.count if notes_res.count is not None else 0
    except Exception:
        notes_count = 0
 
    # quiz stats — placeholder until quiz tables are populated
    quiz_count = 0
    best_score = '—'
 
    return render_template(
        'user_dashboard.html',
        student_name   = session.get('student_name', ''),
        student_email  = session.get('student_email', ''),
        student_dept   = session.get('student_dept', ''),
        student_level  = session.get('student_level', ''),
        student_matric = session.get('student_matric', ''),
        time_greeting  = greeting,
        now            = _dt.now(),
        notes_count    = notes_count,
        quiz_count     = quiz_count,
        best_score     = best_score,
    )


# ── SETTINGS PAGE ────────────────────────────────────────────────────
@app.route('/user/settings', methods=['GET'])
@student_required
def user_settings_page():
    try:
        dept_res    = supabase.table('departments').select('name').order('name').execute()
        departments = [r['name'] for r in dept_res.data] if dept_res.data else []
    except Exception:
        departments = []
 
    return render_template(
        'user_settings.html',
        student_name   = session.get('student_name', ''),
        student_email  = session.get('student_email', ''),
        student_dept   = session.get('student_dept', ''),
        student_level  = session.get('student_level', ''),
        student_matric = session.get('student_matric', ''),
        departments    = departments,
    )
 
 
# ── UPDATE PROFILE ───────────────────────────────────────────────────
@app.route('/user/settings/profile', methods=['POST'])
@student_required
def user_settings_profile():
    uid        = session.get('student_id')
    full_name  = request.form.get('full_name', '').strip()
    matric     = request.form.get('matric_number', '').strip().upper()
    department = request.form.get('department', '').strip()
    level      = request.form.get('level', '').strip()
 
    # validate
    errors = []
    if not full_name:
        errors.append("Full name is required.")
    if not matric:
        errors.append("Matric number is required.")
    elif not re.match(r'^[A-Z0-9][A-Z0-9/\-]{2,19}$', matric):
        errors.append("Matric number format is invalid.")
    if not department:
        errors.append("Please select a department.")
    if level not in ['100', '200', '300', '400']:
        errors.append("Please select a valid level.")
 
    if errors:
        for msg in errors:
            flash(msg, 'error')
        return redirect(url_for('user_settings_page'))
 
    # check matric uniqueness if changed
    try:
        if matric != session.get('student_matric', '').upper():
            check = supabase.table('users').select('id').eq('matric_number', matric).execute()
            if check.data and check.data[0]['id'] != uid:
                flash("That matric number is already in use.", 'error')
                return redirect(url_for('user_settings_page'))
 
        supabase.table('users').update({
            "full_name":     full_name,
            "matric_number": matric,
            "department":    department,
            "level":         level,
            "updated_at":    "now()"
        }).eq('id', uid).execute()
 
        # refresh session
        session['student_name']   = full_name
        session['student_matric'] = matric
        session['student_dept']   = department
        session['student_level']  = level
 
        flash("Profile updated successfully.", 'success')
 
    except Exception as e:
        flash(f"Could not update profile: {str(e)}", 'error')
 
    return redirect(url_for('user_settings_page'))
 
 
# ── CHANGE EMAIL ─────────────────────────────────────────────────────
@app.route('/user/settings/email', methods=['POST'])
@student_required
def user_settings_email():
    uid           = session.get('student_id')
    new_email     = request.form.get('new_email', '').strip().lower()
    confirm_email = request.form.get('confirm_email', '').strip().lower()
    password      = request.form.get('current_password_email', '')
 
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', new_email):
        flash("Enter a valid email address.", 'error')
        return redirect(url_for('user_settings_page'))
 
    if new_email != confirm_email:
        flash("Email addresses do not match.", 'error')
        return redirect(url_for('user_settings_page'))
 
    try:
        user_res = supabase.table('users').select('password_hash').eq('id', uid).single().execute()
        if not check_password_hash(user_res.data['password_hash'], password):
            flash("Current password is incorrect.", 'error')
            return redirect(url_for('user_settings_page'))
 
        # check new email not already taken
        taken = supabase.table('users').select('id').eq('email', new_email).execute()
        if taken.data and taken.data[0]['id'] != uid:
            flash("That email is already registered to another account.", 'error')
            return redirect(url_for('user_settings_page'))
 
        supabase.table('users').update({
            "email":      new_email,
            "updated_at": "now()"
        }).eq('id', uid).execute()
 
        session['student_email'] = new_email
        flash("Email updated successfully.", 'success')
 
    except Exception as e:
        flash(f"Could not update email: {str(e)}", 'error')
 
    return redirect(url_for('user_settings_page'))
 
 
# ── CHANGE PASSWORD ───────────────────────────────────────────────────
@app.route('/user/settings/password', methods=['POST'])
@student_required
def user_settings_password():
    uid         = session.get('student_id')
    current_pw  = request.form.get('current_password', '')
    new_pw      = request.form.get('new_password', '')
    confirm_pw  = request.form.get('confirm_new_password', '')
 
    if len(new_pw) < 8:
        flash("New password must be at least 8 characters.", 'error')
        return redirect(url_for('user_settings_page'))
 
    if new_pw != confirm_pw:
        flash("New passwords do not match.", 'error')
        return redirect(url_for('user_settings_page'))
 
    try:
        user_res = supabase.table('users').select('password_hash').eq('id', uid).single().execute()
        if not check_password_hash(user_res.data['password_hash'], current_pw):
            flash("Current password is incorrect.", 'error')
            return redirect(url_for('user_settings_page'))
 
        supabase.table('users').update({
            "password_hash": generate_password_hash(new_pw),
            "updated_at":    "now()"
        }).eq('id', uid).execute()
 
        flash("Password changed successfully.", 'success')
 
    except Exception as e:
        flash(f"Could not change password: {str(e)}", 'error')
 
    return redirect(url_for('user_settings_page'))
 
 
# ── DELETE ACCOUNT ────────────────────────────────────────────────────
@app.route('/user/settings/delete', methods=['POST'])
@student_required
def user_settings_delete():
    uid      = session.get('student_id')
    password = request.form.get('delete_password', '')
    confirm  = request.form.get('delete_confirm', '').strip()
 
    if confirm.lower() != 'delete my account':
        flash("Type 'delete my account' exactly to confirm deletion.", 'error')
        return redirect(url_for('user_settings_page'))
 
    try:
        user_res = supabase.table('users').select('password_hash').eq('id', uid).single().execute()
        if not check_password_hash(user_res.data['password_hash'], password):
            flash("Incorrect password. Account not deleted.", 'error')
            return redirect(url_for('user_settings_page'))
 
        supabase.table('users').delete().eq('id', uid).execute()
 
        # clear session
        session.clear()
        flash("Your account has been permanently deleted.", 'info')
        return redirect(url_for('user_home'))
 
    except Exception as e:
        flash(f"Could not delete account: {str(e)}", 'error')
        return redirect(url_for('user_settings_page'))





# ── replace with your actual Gemini API key ──────────────────────────
GEMINI_API_KEY = "AIzaSyBgZUIz9pZSsArvg_LPScpqflXcHHrnmNc"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent?key=" + GEMINI_API_KEY
)
 
QUESTIONS_PER_NOTE = 70
 
 
# ── PROMPT ────────────────────────────────────────────────────────────
def _build_prompt(course_code: str, course_title: str) -> str:
    return f"""You are an experienced university lecturer creating a comprehensive exam for the course:
Course Code: {course_code}
Course Title: {course_title}
 
Read the entire attached document carefully from start to finish — every page, every section.
Do NOT focus only on the introduction or headings. Extract assessable content from:
- Definitions and key terms
- Theorems, formulas, and proofs
- Worked examples and their steps
- Case studies and applications
- Diagrams and what they represent
- Any numbered lists or procedures
- Comparisons and classifications
 
Generate exactly {QUESTIONS_PER_NOTE} multiple-choice questions that cover the FULL BREADTH of the document.
Distribute difficulty: roughly 25 easy, 30 medium, 15 hard questions.
 
STRICT RULES:
1. Each question must have exactly 4 options (a, b, c, d).
2. Only one option is correct. The others must be plausible but clearly wrong.
3. Wrong options must NOT be obviously silly — use real-looking distractors.
4. The explanation must state WHY the correct answer is right (1-2 sentences).
5. Do NOT number the questions yourself — just include them in the JSON array.
6. Return ONLY valid JSON. No markdown fences, no preamble, no commentary.
 
Return this exact JSON structure:
{{
  "questions": [
    {{
      "question_text": "...",
      "option_a": "...",
      "option_b": "...",
      "option_c": "...",
      "option_d": "...",
      "correct_option": "a",
      "explanation": "...",
      "difficulty": "easy"
    }}
  ]
}}
 
difficulty must be one of: "easy", "medium", "hard"
correct_option must be one of: "a", "b", "c", "d"
"""
 
 
# ── MAIN GENERATION FUNCTION ──────────────────────────────────────────
def generate_questions_for_note(note: dict, supabase) -> dict:
    """
    Fetch the note file from Supabase storage, send to Gemini,
    parse response, store questions.
 
    Returns:
        {"success": True, "count": N}   on success
        {"success": False, "error": "..."}  on failure
    """
    note_id      = note["id"]
    file_url     = note["file_url"]
    course_code  = note["course_code"]
    course_title = note.get("course_title") or course_code
    department   = note["department"]
    level        = note["level"]
    semester     = note["semester"]
    acad_year    = note["academic_year"]
 
    # ── 1. Mark as processing ─────────────────────────────────────────
    try:
        supabase.table("question_generation_log").upsert({
            "note_id":             note_id,
            "status":              "processing",
            "questions_generated": 0,
            "error_message":       None,
            "updated_at":          "now()"
        }, on_conflict="note_id").execute()
    except Exception as e:
        return {"success": False, "error": f"Log upsert failed: {e}"}
 
    # ── 2. Download the file from Supabase storage ────────────────────
    try:
        resp = requests.get(file_url, timeout=30)
        resp.raise_for_status()
        file_bytes   = resp.content
        content_type = resp.headers.get("Content-Type", "application/pdf")
    except Exception as e:
        _mark_failed(supabase, note_id, f"File download failed: {e}")
        return {"success": False, "error": f"File download failed: {e}"}
 
    # ── 3. Determine MIME type ────────────────────────────────────────
    fname = file_url.split("?")[0].lower()
    if fname.endswith(".pdf"):
        mime = "application/pdf"
    elif fname.endswith(".docx"):
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fname.endswith(".pptx"):
        mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else:
        mime = content_type or "application/pdf"
 
    # ── 4. Build Gemini request ───────────────────────────────────────
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    prompt  = _build_prompt(course_code, course_title)
 
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data":      encoded
                        }
                    },
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature":      0.4,
            "maxOutputTokens":  16384,
            "responseMimeType": "application/json"
        }
    }
 
    # ── 5. Call Gemini ────────────────────────────────────────────────
    try:
        gemini_resp = requests.post(
            GEMINI_URL,
            json=payload,
            timeout=120,
            headers={"Content-Type": "application/json"}
        )
        gemini_resp.raise_for_status()
        gemini_data = gemini_resp.json()
    except Exception as e:
        _mark_failed(supabase, note_id, f"Gemini API error: {e}")
        return {"success": False, "error": f"Gemini API error: {e}"}
 
    # ── 6. Extract text from Gemini response ──────────────────────────
    try:
        raw_text = gemini_data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        _mark_failed(supabase, note_id, f"Gemini response parse error: {e}")
        return {"success": False, "error": f"Gemini response malformed: {e}"}
 
    # ── 7. Parse JSON — strip any accidental markdown fences ──────────
    try:
        cleaned       = re.sub(r"```(?:json)?|```", "", raw_text).strip()
        data          = json.loads(cleaned)
        questions_raw = data.get("questions", [])
    except Exception as e:
        _mark_failed(supabase, note_id, f"JSON parse failed: {e} | Raw: {raw_text[:300]}")
        return {"success": False, "error": f"JSON parse failed: {e}"}
 
    if not questions_raw:
        _mark_failed(supabase, note_id, "Gemini returned 0 questions.")
        return {"success": False, "error": "No questions returned by Gemini."}
 
    # ── 8. Validate each question before inserting ────────────────────
    valid_options = {"a", "b", "c", "d"}
    valid_diff    = {"easy", "medium", "hard"}
    rows          = []
    skipped       = 0
 
    for q in questions_raw:
        qt    = str(q.get("question_text", "")).strip()
        opt_a = str(q.get("option_a", "")).strip()
        opt_b = str(q.get("option_b", "")).strip()
        opt_c = str(q.get("option_c", "")).strip()
        opt_d = str(q.get("option_d", "")).strip()
        correct = str(q.get("correct_option", "")).strip().lower()
        expl    = str(q.get("explanation", "")).strip()
        diff    = str(q.get("difficulty", "medium")).strip().lower()
 
        if not all([qt, opt_a, opt_b, opt_c, opt_d]):
            skipped += 1
            continue
        if correct not in valid_options:
            skipped += 1
            continue
        if diff not in valid_diff:
            diff = "medium"
 
        rows.append({
            "note_id":        note_id,
            "course_code":    course_code,
            "department":     department,
            "level":          level,
            "semester":       semester,
            "academic_year":  acad_year,
            "question_text":  qt,
            "option_a":       opt_a,
            "option_b":       opt_b,
            "option_c":       opt_c,
            "option_d":       opt_d,
            "correct_option": correct,
            "explanation":    expl,
            "difficulty":     diff,
            "status":         "pending",
            "created_at":     "now()"
        })
 
    if not rows:
        _mark_failed(supabase, note_id, "All questions failed validation.")
        return {"success": False, "error": "All questions failed validation."}
 
    # ── 9. Bulk insert in batches of 25 ───────────────────────────────
    try:
        for i in range(0, len(rows), 25):
            supabase.table("questions").insert(rows[i:i + 25]).execute()
    except Exception as e:
        _mark_failed(supabase, note_id, f"DB insert failed: {e}")
        return {"success": False, "error": f"DB insert failed: {e}"}
 
    # ── 10. Mark done ─────────────────────────────────────────────────
    count = len(rows)
    try:
        supabase.table("question_generation_log").upsert({
            "note_id":             note_id,
            "status":              "done",
            "questions_generated": count,
            "error_message":       None,
            "updated_at":          "now()"
        }, on_conflict="note_id").execute()
    except Exception:
        pass
 
    return {"success": True, "count": count, "skipped": skipped}
 
 
# ── HELPER ────────────────────────────────────────────────────────────
def _mark_failed(supabase, note_id: int, error_msg: str):
    try:
        supabase.table("question_generation_log").upsert({
            "note_id":       note_id,
            "status":        "failed",
            "error_message": error_msg[:500],
            "updated_at":    "now()"
        }, on_conflict="note_id").execute()
    except Exception:
        pass

# ── TRIGGER GENERATION FOR ONE NOTE ──────────────────────────────────
@app.route('/admin/quiz/generate/<int:note_id>', methods=['POST'])
@admin_required
def admin_generate_questions(note_id):
    # prevent double-generation: check if already done
    try:
        log = supabase.table('question_generation_log') \
            .select('status, questions_generated') \
            .eq('note_id', note_id).execute()
 
        if log.data:
            existing = log.data[0]
            if existing['status'] == 'done':
                flash(
                    f"Questions already generated for this note "
                    f"({existing['questions_generated']} questions). "
                    f"To regenerate, delete existing questions first.",
                    'error'
                )
                return redirect(url_for('admin_questions_review'))
 
            if existing['status'] == 'processing':
                flash("Generation already in progress for this note.", 'error')
                return redirect(url_for('admin_questions_review'))
 
    except Exception:
        pass  # log may not exist yet — continue
 
    # fetch the note
    try:
        note_res = supabase.table('notes').select('*').eq('id', note_id).single().execute()
        note = note_res.data
        if not note:
            flash("Note not found.", 'error')
            return redirect(url_for('admin_questions_review'))
    except Exception as e:
        flash(f"Could not fetch note: {str(e)}", 'error')
        return redirect(url_for('admin_questions_review'))
 
    # run generation in background thread so page returns immediately
    def _run_generation(n, sb):
        generate_questions_for_note(n, sb)
 
    t = threading.Thread(target=_run_generation, args=(note, supabase), daemon=True)
    t.start()
 
    flash(
        f"Generation started for {note['course_code']}. "
        f"Refresh the page in 60 seconds to see the results.",
        'info'
    )
    return redirect(url_for('admin_questions_review'))
 
 
# ── ADMIN QUESTION REVIEW PAGE ────────────────────────────────────────
@app.route('/admin/questions', methods=['GET'])
@admin_required
def admin_questions_review():
    """
    Shows:
    - All notes with generation status
    - Filterable question list (by status, course code, difficulty)
    """
    # filter params
    status_filter  = request.args.get('status', 'pending')   # pending | approved | flagged | all
    course_filter  = request.args.get('course_code', '').strip().upper()
    diff_filter    = request.args.get('difficulty', '').strip()
    search_q       = request.args.get('search', '').strip()
 
    # ── fetch notes with generation log joined ────────────────────────
    try:
        notes_res = supabase.table('notes') \
            .select('id, course_code, course_title, department, level, semester, academic_year') \
            .order('created_at', desc=True).execute()
        notes_list = notes_res.data or []
 
        # attach generation status to each note
        log_res = supabase.table('question_generation_log') \
            .select('note_id, status, questions_generated, updated_at').execute()
        log_map = {r['note_id']: r for r in (log_res.data or [])}
 
        for note in notes_list:
            log = log_map.get(note['id'])
            note['gen_status']  = log['status']              if log else 'not_started'
            note['gen_count']   = log['questions_generated'] if log else 0
            note['gen_updated'] = log['updated_at']          if log else None
 
    except Exception as e:
        flash(f"Could not load notes: {str(e)}", 'error')
        notes_list = []
 
    # ── fetch questions with filters ──────────────────────────────────
    try:
        q = supabase.table('questions').select(
            'id, note_id, course_code, department, level, semester, '
            'question_text, option_a, option_b, option_c, option_d, '
            'correct_option, explanation, difficulty, status, created_at'
        )
 
        if status_filter and status_filter != 'all':
            q = q.eq('status', status_filter)
        if course_filter:
            q = q.eq('course_code', course_filter)
        if diff_filter:
            q = q.eq('difficulty', diff_filter)
        if search_q:
            q = q.ilike('question_text', f'%{search_q}%')
 
        q = q.order('created_at', desc=True)
        questions_res = q.execute()
        questions_list = questions_res.data or []
 
    except Exception as e:
        flash(f"Could not load questions: {str(e)}", 'error')
        questions_list = []
 
    # ── counts for tab badges ─────────────────────────────────────────
    try:
        counts_res = supabase.table('questions').select('status').execute()
        status_counts = Counter(r['status'] for r in (counts_res.data or []))
    except Exception:
        status_counts = {}
 
    # ── distinct course codes for filter dropdown ─────────────────────
    try:
        cc_res = supabase.table('questions').select('course_code').execute()
        course_codes = sorted(set(r['course_code'] for r in (cc_res.data or [])))
    except Exception:
        course_codes = []
 
    return render_template(
        'admin_questions.html',
        notes_list     = notes_list,
        questions_list = questions_list,
        status_counts  = status_counts,
        course_codes   = course_codes,
        filters={
            'status':      status_filter,
            'course_code': course_filter,
            'difficulty':  diff_filter,
            'search':      search_q,
        }
    )
 
 
# ── APPROVE A QUESTION ────────────────────────────────────────────────
@app.route('/admin/questions/approve/<int:q_id>', methods=['POST'])
@admin_required
def admin_approve_question(q_id):
    try:
        supabase.table('questions').update({
            'status': 'approved'
        }).eq('id', q_id).execute()
        return jsonify({'success': True, 'new_status': 'approved'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
 
 
# ── FLAG A QUESTION ───────────────────────────────────────────────────
@app.route('/admin/questions/flag/<int:q_id>', methods=['POST'])
@admin_required
def admin_flag_question(q_id):
    try:
        supabase.table('questions').update({
            'status': 'flagged'
        }).eq('id', q_id).execute()
        return jsonify({'success': True, 'new_status': 'flagged'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
 
 
# ── EDIT A QUESTION (inline) ──────────────────────────────────────────
@app.route('/admin/questions/edit/<int:q_id>', methods=['POST'])
@admin_required
def admin_edit_question(q_id):
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data'}), 400
 
    allowed = {
        'question_text', 'option_a', 'option_b', 'option_c', 'option_d',
        'correct_option', 'explanation', 'difficulty'
    }
    update = {k: v for k, v in data.items() if k in allowed}
 
    if not update:
        return jsonify({'success': False, 'error': 'Nothing to update'}), 400
 
    # basic validation
    if 'correct_option' in update and update['correct_option'] not in 'abcd':
        return jsonify({'success': False, 'error': 'Invalid correct_option'}), 400
 
    try:
        supabase.table('questions').update(update).eq('id', q_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
 
 
# ── DELETE A QUESTION ─────────────────────────────────────────────────
@app.route('/admin/questions/delete/<int:q_id>', methods=['POST'])
@admin_required
def admin_delete_question(q_id):
    try:
        supabase.table('questions').delete().eq('id', q_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
 
 
# ── APPROVE ALL PENDING FOR A NOTE ───────────────────────────────────
@app.route('/admin/questions/approve-all/<int:note_id>', methods=['POST'])
@admin_required
def admin_approve_all(note_id):
    try:
        supabase.table('questions') \
            .update({'status': 'approved'}) \
            .eq('note_id', note_id) \
            .eq('status', 'pending') \
            .execute()
        flash("All pending questions for this note approved.", 'success')
    except Exception as e:
        flash(f"Error: {str(e)}", 'error')
    return redirect(url_for('admin_questions_review'))
 
 
# ── DELETE ALL QUESTIONS FOR A NOTE (to regenerate) ──────────────────
@app.route('/admin/questions/delete-all/<int:note_id>', methods=['POST'])
@admin_required
def admin_delete_all_questions(note_id):
    try:
        supabase.table('questions').delete().eq('note_id', note_id).execute()
        supabase.table('question_generation_log').delete().eq('note_id', note_id).execute()
        flash("All questions deleted. You can now regenerate.", 'success')
    except Exception as e:
        flash(f"Error: {str(e)}", 'error')
    return redirect(url_for('admin_questions_review'))



if __name__ == '__main__':
    app.run(debug=True)
