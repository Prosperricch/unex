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

if __name__ == '__main__':
    app.run(debug=True)
