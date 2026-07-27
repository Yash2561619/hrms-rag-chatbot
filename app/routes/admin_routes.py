import logging
import os
import boto3
from functools import wraps
import threading
import chromadb
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from app.services.auth_service import authenticate_admin
from app.services.s3_service import (
    delete_file_from_s3,
    generate_presigned_url,
    upload_policy_to_s3,
    upload_salary_to_s3,
    upload_video_to_s3,
)
from app.services.whatsapp_service import send_text
from database import (
    add_employee,
    can_approve_leave,
    delete_employee,
    get_all_employees,
    get_all_leave_requests,
    get_all_policy_files,
    get_all_salary_slips,
    get_all_training_videos,
    get_connection,
    get_dashboard_stats,
    get_department_employee_counts,
    get_employee,
    get_leave_details,
    get_leave_status_counts,
    get_monthly_leave_data,
    get_recent_activities,
    log_activity,
    save_policy_file,
    save_salary_slip,
    save_training_video,
    update_employee,
    update_leave_status,
)
from scripts.update_db import build_index, get_pdf_hash, get_pdf_version
from validators import ValidationError, validate_phone

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)

from scripts.update_db import load_pdf_registry, save_pdf_registry
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)

# --- ChromaDB Setup (Direct Collection Reference) ---
chroma_client = chromadb.PersistentClient(path="chroma_db")
collection = chroma_client.get_or_create_collection(name="hr_policies")

# =====================================================
# DASHBOARD
# =====================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_id = session.get('admin_id')

        if not admin_id:
            flash('🔐 Please sign in to continue.', 'warning')
            return redirect(url_for('admin.login'))

        return f(*args, **kwargs)

    return decorated_function


@admin_bp.route('/')
def home():
    if session.get('admin_id'):
        return redirect(url_for('admin.admin_dashboard'))
    return redirect(url_for('admin.login'))


from flask import render_template, request, redirect, url_for, flash, session

@admin_bp.route('/admin/login', methods=['GET', 'POST'])
def login():
    # Only redirect if session is valid
    if request.method == 'GET' and session.get('admin_id'):
        return redirect(url_for('admin.admin_dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        logger.info(f'LOGIN_ATTEMPT | email={email}')

        admin = authenticate_admin(email, password)

        logger.info(f'AUTH_RESULT = {admin}')

        if admin:
            # Create fresh session
            session.clear()

            session['admin_id'] = admin['id']
            session['admin_name'] = admin['name']
            session['admin_email'] = admin['email']
            session['admin_role'] = admin['role']

            session.permanent = True
            session.modified = True

            logger.info(f'LOGIN_SUCCESS | admin={email}')

            return redirect(url_for('admin.admin_dashboard'))

        logger.warning(f'LOGIN_FAILED | email={email}')
        flash('❌ Invalid email or password', 'danger')

    return render_template('login.html')


@admin_bp.route('/admin/logout')
def logout():
    admin_name = session.get('admin_name', 'Admin')

    # Clear all session data
    session.clear()

    # Force session removal
    session.pop('admin_id', None)
    session.pop('admin_name', None)
    session.pop('admin_email', None)
    session.pop('admin_role', None)

    response = redirect(url_for('admin.login'))

    # Remove session cookie
    response.set_cookie(
        current_app.config.get('SESSION_COOKIE_NAME', 'session'),
        '',
        expires=0
    )

    flash(
        f'👋 {admin_name} signed out successfully.',
        'success'
    )

    return response


@admin_bp.route('/admin')
@login_required
def admin_dashboard():
    """Admin dashboard with statistics"""
    
    try:
        stats = get_dashboard_stats()
        leave_status = get_leave_status_counts()
        monthly_leave = get_monthly_leave_data()
        department_data = get_department_employee_counts()
        activities = get_recent_activities()

        logger.info('ADMIN_DASHBOARD_LOADED')

        return render_template(
            'dashboard.html',
            stats=stats,
            leave_status=leave_status,
            monthly_leave=monthly_leave,
            department_data=department_data,
            activities=activities
        )

    except Exception as e:
        logger.exception('ADMIN_DASHBOARD_ERROR')
        flash('❌ Error loading dashboard')
        return 'Error loading dashboard', 500


# =====================================================
# EMPLOYEES
# =====================================================

@admin_bp.route('/employees')
@login_required
def employees():
    """List all employees with search"""
    
    search = request.args.get('search', '').strip()
    employees_list = get_all_employees(search)

    return render_template(
        'employees.html',
        employees=employees_list,
        search=search
    )


@admin_bp.route('/add-employee', methods=['GET', 'POST'])
@login_required
def add_employee_page():
    """Add new employee"""

    if request.method == 'POST':

        try:
            # ===============================
            # Get form data
            # ===============================

            employee_id = request.form.get(
                'employee_id',
                ''
            ).strip()

            name = request.form.get(
                'name',
                ''
            ).strip()

            country_code = request.form.get(
                'country_code',
                '91'
            ).strip()

            phone = request.form.get(
                'whatsapp',
                ''
            ).strip()

            manager = request.form.get(
                'manager',
                ''
            ).strip()

            department = request.form.get(
                'department',
                ''
            ).strip()

            # ===============================
            # Validate required fields
            # ===============================

            if not all([
                employee_id,
                name,
                phone,
                manager,
                department
            ]):
                flash(
                    '❌ All fields are required',
                    'danger'
                )
                return render_template('add_employee.html')

            # ===============================
            # Validate phone number
            # User enters only 10 digits
            # Final format: 91XXXXXXXXXX
            # ===============================

            whatsapp = validate_phone(
                country_code,
                phone
            )

            # ===============================
            # Add employee to database
            # ===============================

            add_employee(
                employee_id,
                name,
                whatsapp,
                manager,
                department
            )

            # ===============================
            # Log activity
            # ===============================

            log_activity(
                f'👤 New employee added: {name} ({employee_id})'
            )

            # ===============================
            # Success popup message
            # ===============================

            flash(
                '✅ Employee added successfully!',
                'success'
            )

            return redirect(
                url_for('admin.employees')
            )

        except ValidationError as e:

            logger.warning(
                f'VALIDATION_ERROR: {str(e)}'
            )

            flash(
                f'❌ {str(e)}',
                'danger'
            )

        except Exception as e:

            logger.exception('ADD_EMPLOYEE_ERROR')

            flash(
                '❌ Failed to add employee',
                'danger'
            )

    return render_template('add_employee.html')


@admin_bp.route('/edit-employee/<employee_id>', methods=['GET', 'POST'])
@login_required
def edit_employee(employee_id):
    """Edit employee details"""

    if request.method == 'POST':

        try:
            # ===============================
            # Get form data
            # ===============================

            name = request.form.get('name', '').strip()

            country_code = request.form.get(
                'country_code',
                '91'
            ).strip()

            whatsapp = request.form.get(
                'whatsapp',
                ''
            ).strip()

            manager = request.form.get(
                'manager',
                ''
            ).strip()

            department = request.form.get(
                'department',
                ''
            ).strip()

            # ===============================
            # Validate required fields
            # ===============================

            if not all([name, whatsapp, manager, department]):

                flash(
                    '❌ All fields are required',
                    'danger'
                )

                return render_template(
                    'edit_employee.html',
                    employee=(
                        employee_id,
                        name,
                        whatsapp,
                        manager,
                        department
                    )
                )

            # ===============================
            # Validate phone
            # User enters only 10 digits
            # Final format: 91XXXXXXXXXX
            # ===============================

            whatsapp = validate_phone(
                country_code,
                whatsapp
            )

            # ===============================
            # Update employee
            # ===============================

            update_employee(
                employee_id,
                name,
                whatsapp,
                manager,
                department
            )

            log_activity(
                f'✏️ Employee updated: {name} ({employee_id})'
            )

            flash(
                '✅ Employee updated successfully!',
                'success'
            )

            return redirect(url_for('admin.employees'))

        except ValidationError as e:

            logger.warning(
                f'VALIDATION_ERROR: {str(e)}'
            )

            flash(
                f'❌ {str(e)}',
                'danger'
            )

        except Exception:

            logger.exception('EDIT_EMPLOYEE_ERROR')

            flash(
                '❌ Failed to update employee',
                'danger'
            )

    employee = get_employee(employee_id)

    if not employee:

        flash(
            '❌ Employee not found',
            'danger'
        )

        return redirect(url_for('admin.employees'))

    return render_template(
        'edit_employee.html',
        employee=employee
    )

@admin_bp.route('/delete-employee/<employee_id>')
@login_required
def delete_employee_route(employee_id):
    """Delete employee"""
    
    try:
        employee = get_employee(employee_id)
        
        if not employee:
            flash('❌ Employee not found')
            return redirect(url_for('admin.employees'))

        delete_employee(employee_id)
        
        log_activity(f'🗑️ Employee deleted: {employee[1]} ({employee_id})')
        flash('✅ Employee deleted successfully!')

    except Exception as e:
        logger.exception('DELETE_EMPLOYEE_ERROR')
        flash('❌ Failed to delete employee')

    return redirect(url_for('admin.employees'))


# =====================================================
# LEAVE MANAGEMENT
# =====================================================

@admin_bp.route('/leave-requests')
@login_required
def leave_requests():
    """List all leave requests"""
    
    try:
        leaves = get_all_leave_requests()

        if not leaves:
            pending_count = 0
            approved_count = 0
            active_count = 0
        else:
            pending_count = sum(1 for l in leaves if l[7] == 'Pending')
            approved_count = sum(1 for l in leaves if l[7] == 'Approved')
            active_count = approved_count

        return render_template(
            'leave_requests.html',
            leaves=leaves,
            pending_count=pending_count,
            approved_count=approved_count,
            active_count=active_count
        )

    except Exception as e:
        logger.exception('LEAVE_REQUESTS_PAGE_ERROR')
        flash('❌ Failed to load leave requests')
        return redirect(url_for('admin.admin_dashboard'))


@admin_bp.route('/approve-leave/<int:request_id>')
@login_required
def approve_leave(request_id):
    """Approve leave request"""
    
    try:
        leave = get_leave_details(request_id)

        if not leave:
            logger.warning(f'Leave request {request_id} not found')
            flash('❌ Leave request not found')
            return redirect(url_for('admin.leave_requests'))

        # Check leave balance
        allowed, message = can_approve_leave(
            leave['employee_id'],
            leave['leave_type'],
            leave['leave_days']
        )

        if not allowed:
            logger.warning(f'LEAVE_APPROVAL_BLOCKED: {message}')
            log_activity(
                f'❌ Leave approval blocked for {leave["name"]} ({message})'
            )
            flash(f'❌ Cannot approve: {message}')
            return redirect(url_for('admin.leave_requests'))

        # Approve leave
        update_leave_status(request_id, 'Approved')

        # Get updated details
        leave = get_leave_details(request_id)

        # Log activity
        log_activity(f'✅ Leave approved for {leave["name"]}')

        # Notify employee
        send_text(
            leave['whatsapp'],
            f'''✅ Leave Approved

Employee: {leave["name"]}
Type: {leave["leave_type"]}

From: {leave["from_date"]}
To: {leave["to_date"]}

Total Days: {leave["leave_days"]}

Approved by: {leave["manager"]}
Status: Approved ✓
'''
        )

        flash('✅ Leave approved successfully!')
        logger.info(f'Leave {request_id} approved')

    except Exception as e:
        logger.exception(f'APPROVE_LEAVE_ERROR: {request_id}')
        flash('❌ Failed to approve leave')

    return redirect(url_for('admin.leave_requests'))


@admin_bp.route('/reject-leave/<int:request_id>')
@login_required
def reject_leave(request_id):
    """Reject leave request"""
    
    try:
        # Update status first
        update_leave_status(request_id, 'Rejected')

        # Get leave details
        leave = get_leave_details(request_id)

        if leave:
            # Log activity
            log_activity(f'❌ Leave rejected for {leave["name"]}')

            # Notify employee
            send_text(
                leave['whatsapp'],
                f'''❌ Leave Rejected

Employee: {leave["name"]}

From: {leave["from_date"]}
To: {leave["to_date"]}

Reason:
{leave["reason"]}

Rejected by:
{leave["manager"]}

Status: Rejected ✗
'''
            )

        flash('✅ Leave rejected successfully!')
        logger.info(f'Leave {request_id} rejected')

    except Exception as e:
        logger.exception(f'REJECT_LEAVE_ERROR: {request_id}')
        flash('❌ Failed to reject leave')

    return redirect(url_for('admin.leave_requests'))




# =====================================================
# SALARY SLIPS (Optional routes - add if needed)
# =====================================================

@admin_bp.route('/upload-salary', methods=['GET', 'POST'])
@login_required
def upload_salary():
    """Upload salary slip"""
    
    if request.method == 'POST':
        
        try:
            employee_id = request.form.get('employee_id', '').strip()
            month = request.form.get('month')
            year = int(request.form.get('year'))

            month_map = {
              'January': 1,
              'February': 2,
              'March': 3,
              'April': 4,
              'May': 5,
              'June': 6,
              'July': 7,
              'August': 8,
              'September': 9,
              'October': 10,
              'November': 11,
              'December': 12
}
            if str(month).isdigit():
               month = int(month)
            else:
               month = month_map[month]

            if 'salary_pdf' not in request.files:
                flash('❌ No file selected')
                return redirect(url_for('admin.upload_salary'))

            file = request.files['salary_pdf']

            if not file or file.filename == '':
                flash('❌ No file selected')
                return redirect(url_for('admin.upload_salary'))

            if not file.filename.endswith('.pdf'):
                flash('❌ Only PDF files allowed')
                return redirect(url_for('admin.upload_salary'))

            # Save file
            from app.services.s3_service import upload_salary_to_s3

# Generate filename
            filename = secure_filename(f'{employee_id}_{month}_{year}.pdf')

# Upload directly to S3
            s3_key = upload_salary_to_s3(file, filename)

            logger.info(f'SALARY_UPLOADED_TO_S3 | key={s3_key}')

# Save S3 key in database
            save_salary_slip(employee_id, month, year, s3_key)

            log_activity(f'💰 Salary slip uploaded for {employee_id} ({month}/{year})')
            flash('✅ Salary slip uploaded successfully!')

            return redirect(url_for('admin.upload_salary'))

        except Exception as e:
            logger.exception('UPLOAD_SALARY_ERROR')
            flash('❌ Failed to upload salary slip')

    employees_list = get_all_employees()
    salary_slips = get_all_salary_slips()

    return render_template(
        'upload_salary.html',
        employees=employees_list,
        salary_slips=salary_slips
    )


# =====================================================
# POLICY MANAGEMENT
# =====================================================
import gc
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


def run_background_index_safe():
    """Waits 1 second for the HTTP request to completely release RAM & file handles,

    then cleans garbage and builds the vector index.
    """
    time.sleep(1)  # Allow HTTP response to flush completely
    gc.collect()  # Clean up memory from file read operations
    try:
        logger.info("BACKGROUND_INDEX_START | Triggered from admin upload")
        from scripts.update_db import build_index

        build_index()
        logger.info("BACKGROUND_INDEX_COMPLETE")
    except Exception as e:
        logger.error(f"BACKGROUND_INDEX_FAILED | error={e}")
    finally:
        gc.collect()  # Extra garbage collection pass


@admin_bp.route("/policy-management", methods=["GET", "POST"])
@login_required
def policy_management():
    """Manage HR policies with S3 storage and background ChromaDB indexing."""
    POLICY_FOLDER = current_app.config.get(
        "POLICY_FOLDER", "uploads/policies"
    )
    os.makedirs(POLICY_FOLDER, exist_ok=True)

    if request.method == "POST":
        if "policy" not in request.files:
            flash("❌ No file selected", "danger")
            return redirect(url_for("admin.policy_management"))

        file = request.files["policy"]

        if not file or file.filename == "":
            flash("❌ No file selected", "danger")
            return redirect(url_for("admin.policy_management"))

        if not file.filename.lower().endswith(".pdf"):
            flash("❌ Only PDF files allowed", "danger")
            return redirect(url_for("admin.policy_management"))

        temp_filepath = None
        try:
            filename = secure_filename(file.filename)
            temp_filepath = os.path.join(POLICY_FOLDER, filename)

            # 1. Save file locally temporarily to calculate hash
            file.save(temp_filepath)

            file_hash = get_pdf_hash(temp_filepath)
            version = get_pdf_version(filename)

            # 2. Upload file stream to S3
            with open(temp_filepath, "rb") as upload_file:
                s3_key = upload_policy_to_s3(upload_file, filename)

            # 3. Save metadata record to database
            save_policy_file(
                file_name=filename,
                s3_key=s3_key,
                version=version,
                file_hash=file_hash,
            )

            logger.info(f"POLICY_UPLOADED_TO_S3 | key={s3_key}")

            # 4. START SAFE BACKGROUND THREAD HERE
            threading.Thread(
                target=run_background_index_safe, daemon=True
            ).start()

            log_activity(f"📚 Policy uploaded: {filename}")

            flash(
                "✅ Policy uploaded! Knowledge base is updating in the background.",
                "success",
            )

            # 5. RETURN IMMEDIATELY (Prevents Gunicorn 120s timeout)
            return redirect(url_for("admin.policy_management"))

        except Exception as e:
            logger.exception(f"UPLOAD_POLICY_ERROR | error={e}")
            flash("❌ Failed to upload policy", "danger")

        finally:
            # Clean up local temp file
            if temp_filepath and os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError:
                    pass

    policies = get_all_policy_files()
    return render_template("policy_management.html", policies=policies)


@admin_bp.route("/delete-policy/<path:filename>", methods=["GET", "POST"])
def delete_policy(filename):
    try:
        # 1. Delete from S3
        s3_client.delete_object(
            Bucket=S3_BUCKET_NAME, Key=f"policies/{filename}"
        )
        logger.info(f"S3_DELETED | file={filename}")

        # 2. Delete vectors from ChromaDB
        collection.delete(where={"source": filename})
        logger.info(f"CHROMADB_DELETED | file={filename}")

        # 3. Update Registry JSON
        registry = load_pdf_registry()
        if filename in registry:
            del registry[filename]
            save_pdf_registry(registry)
            logger.info(f"REGISTRY_DELETED | file={filename}")

        flash(f"Successfully deleted {filename}", "success")
    except Exception as e:
        logger.error(f"DELETE_FAILED | file={filename} | error={e}")
        flash(f"Failed to delete {filename}: {str(e)}", "danger")

    return redirect(url_for("policy_management"))

@admin_bp.route("/download-policy/<filename>")
@login_required
def download_policy(filename):
    s3_key = f"policies/{filename}"
    url = generate_presigned_url(s3_key)
    return redirect(url)


@admin_bp.route("/view-policy/<filename>")
@login_required
def view_policy(filename):
    s3_key = f"policies/{filename}"
    url = generate_presigned_url(s3_key)
    return redirect(url)


@admin_bp.route("/view-salary/<int:id>")
@login_required
def view_salary_route(id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT file_path
        FROM salary_slips
        WHERE id=?
    """, (id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        flash("❌ Salary slip not found.")
        return redirect(url_for("admin.upload_salary"))

    s3_key = row[0]

    # Generate temporary S3 URL
    url = generate_presigned_url(s3_key)

    # Open PDF directly from S3
    return redirect(url)


@admin_bp.route("/delete-salary/<int:id>")
@login_required
def delete_salary_route(id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT file_path
        FROM salary_slips
        WHERE id=?
    """, (id,))

    row = cursor.fetchone()

    if not row:
        conn.close()
        flash("❌ Salary slip not found.")
        return redirect(url_for("admin.upload_salary"))

    s3_key = row[0]

    # Delete file from S3
    delete_file_from_s3(s3_key)

    # Delete database record
    cursor.execute("""
        DELETE FROM salary_slips
        WHERE id=?
    """, (id,))

    conn.commit()
    conn.close()

    flash("✅ Salary slip deleted successfully!")

    log_activity(f"Deleted salary slip: {s3_key}")

    return redirect(url_for("admin.upload_salary"))

@admin_bp.route("/upload-video", methods=["GET", "POST"])
@login_required
def upload_video():

    if request.method == "POST":

        title = request.form.get("title")
        category = request.form.get("category")

        file = request.files["video"]

        if file.filename == "":
            flash("Select a video")
            return redirect(request.url)

        filename = secure_filename(file.filename)

        s3_key = upload_video_to_s3(file, filename)

        print("=" * 50)
        print("TITLE:", title)
        print("CATEGORY:", category)
        print("S3 KEY:", s3_key)
        print("=" * 50)

        save_training_video(
            title=title,
            category=category,
            s3_key=s3_key
        )
        logger.info("VIDEO_SAVED_IN_DATABASE")
        print("SAVE FUNCTION COMPLETED")

        flash("✅ Video uploaded successfully")
        return redirect(url_for("admin.upload_video"))

    videos = get_all_training_videos()

    return render_template(
        "upload_video.html",
        videos=videos
    )