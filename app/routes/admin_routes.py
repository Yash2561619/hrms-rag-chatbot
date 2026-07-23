import os
import logging
from database import get_connection

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    current_app,
    send_file,
    send_from_directory
)
from werkzeug.utils import secure_filename
from functools import wraps
from app.services.auth_service import authenticate_admin
# Database functions
from database import (
    get_dashboard_stats,
    get_leave_status_counts,
    get_monthly_leave_data,
    get_department_employee_counts,
    get_recent_activities,
    get_all_employees,
    add_employee,
    update_employee,
    delete_employee,
    get_employee,
    get_all_leave_requests,
    get_leave_details,
    can_approve_leave,
    update_leave_status,
    save_salary_slip,
    get_all_salary_slips,
    log_activity
)
from flask import current_app
# Validation
from validators import validate_phone, ValidationError

# WhatsApp service
from app.services.whatsapp_service import send_text

# Chroma rebuild


logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)


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

@admin_bp.route('/policy-management', methods=['GET', 'POST'])
@login_required
def policy_management():
    """Manage HR policies"""
    
    POLICY_FOLDER = 'uploads/policies'
    os.makedirs(POLICY_FOLDER, exist_ok=True)

    if request.method == 'POST':
        
        if 'policy' not in request.files:
            flash('❌ No file selected')
            return redirect(url_for('admin.policy_management'))

        file = request.files['policy']

        if not file or file.filename == '':
            flash('❌ No file selected')
            return redirect(url_for('admin.policy_management'))

        if not file.filename.endswith('.pdf'):
            flash('❌ Only PDF files allowed')
            return redirect(url_for('admin.policy_management'))

        try:
            filename = secure_filename(file.filename)
            filepath = os.path.join(POLICY_FOLDER, filename)
            file.save(filepath)

            from scripts.update_db import build_index
            # Rebuild index
            build_index()

            log_activity(f'📚 Policy uploaded: {filename}')
            flash('✅ Policy uploaded and indexed successfully!')

            return redirect(url_for('admin.policy_management'))

        except Exception as e:
            logger.exception('UPLOAD_POLICY_ERROR')
            flash('❌ Failed to upload policy')

    # Get list of policies
    policies = []
    if os.path.exists(POLICY_FOLDER):
        policies = sorted(
            [f for f in os.listdir(POLICY_FOLDER) if f.endswith('.pdf')],
            reverse=True
        )

    return render_template(
        'policy_management.html',
        policies=policies
    )


@admin_bp.route('/delete-policy/<filename>')
@login_required
def delete_policy(filename):
    """Delete policy file"""
    
    POLICY_FOLDER = current_app.config['POLICY_FOLDER']
    
    try:
        filepath = os.path.join(POLICY_FOLDER, secure_filename(filename))

        if not os.path.exists(filepath):
            flash('❌ File not found')
            return redirect(url_for('admin.policy_management'))

        os.remove(filepath)

        from scripts.update_db import build_index

        # Rebuild index to remove chunks
        build_index()

        log_activity(f'📚 Policy deleted: {filename}')
        flash(f'✅ {filename} deleted successfully!')

    except Exception as e:
        logger.exception('DELETE_POLICY_ERROR')
        flash('❌ Failed to delete policy')

    return redirect(url_for('admin.policy_management'))


@admin_bp.route("/download-policy/<filename>")
@login_required
def download_policy(filename):

    return send_from_directory(
        current_app.config['POLICY_FOLDER'],
        filename,
        as_attachment=True
    )


@admin_bp.route("/view-policy/<filename>")
@login_required
def view_policy(filename):

    return send_from_directory(
        current_app.config['POLICY_FOLDER'],
        filename
    )

@admin_bp.route('/view-salary/<int:id>')
@login_required
def view_salary_route(id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        'SELECT file_path FROM salary_slips WHERE id=?',
        (id,)
    )

    row = cursor.fetchone()
    conn.close()

    if not row:
        flash('Salary slip not found.')
        return redirect(url_for('admin.upload_salary'))

    filepath = row[0]

    return send_file(
        filepath,
        mimetype='application/pdf'
    )



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

    if row:

        filepath = row[0]

        if os.path.exists(filepath):
            os.remove(filepath)

        cursor.execute("""
            DELETE FROM salary_slips
            WHERE id=?
        """, (id,))

        conn.commit()

        flash("✅ Salary slip deleted successfully!")

        log_activity(f"🗑 Deleted salary slip ID {id}")

    else:

        flash("❌ Salary slip not found.")

    conn.close()

    return redirect(url_for('admin.upload_salary'))
 