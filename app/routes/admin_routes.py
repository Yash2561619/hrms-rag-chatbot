"""Admin Blueprint Routes for Employee, Leave, Salary, Policy, and Video Management.

Location: app/routes/admin_routes.py
"""

from functools import wraps
import logging
import os
from datetime import datetime

import boto3
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
from redis import Redis
from rq import Queue
from werkzeug.utils import secure_filename

from app.services.auth_service import authenticate_admin
from app.services.s3_service import (
    delete_file_from_s3,
    generate_presigned_url,
    sync_faiss_from_s3,
    upload_policy_to_s3,
    upload_salary_to_s3,
    upload_video_to_s3,
)
from app.services.whatsapp_service import send_text
from app.tasks.salary_tasks import process_bulk_salary_slips_job
from app.utils.pdf_security import (
    generate_salary_pdf_password,
    protect_pdf_with_password,
)
from database import (
    add_employee,
    can_approve_leave,
    delete_employee,
    delete_policy_file,
    delete_training_video,
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
from scripts.update_db import (
    get_pdf_hash,
    get_pdf_version,
    load_pdf_registry,
    save_pdf_registry,
)
from validators import ValidationError, validate_phone

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)

# Redis Queue Connection for Background Tasks
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_conn = Redis.from_url(REDIS_URL)
salary_task_queue = Queue("hr_tasks", connection=redis_conn)


# =====================================================
# AUTH & DASHBOARD
# =====================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_id = session.get("admin_id")
        if not admin_id:
            flash("🔐 Please sign in to continue.", "warning")
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)

    return decorated_function


@admin_bp.route("/")
def home():
    if session.get("admin_id"):
        return redirect(url_for("admin.admin_dashboard"))
    return redirect(url_for("admin.login"))


@admin_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and session.get("admin_id"):
        return redirect(url_for("admin.admin_dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        logger.info(f"LOGIN_ATTEMPT | email={email}")
        admin = authenticate_admin(email, password)

        if admin:
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_name"] = admin["name"]
            session["admin_email"] = admin["email"]
            session["admin_role"] = admin["role"]
            session.permanent = True
            session.modified = True

            logger.info(f"LOGIN_SUCCESS | admin={email}")
            return redirect(url_for("admin.admin_dashboard"))

        logger.warning(f"LOGIN_FAILED | email={email}")
        flash("❌ Invalid email or password", "danger")

    return render_template("login.html")


@admin_bp.route("/admin/logout")
def logout():
    admin_name = session.get("admin_name", "Admin")
    session.clear()
    response = redirect(url_for("admin.login"))
    response.set_cookie(
        current_app.config.get("SESSION_COOKIE_NAME", "session"),
        "",
        expires=0,
    )
    flash(f"👋 {admin_name} signed out successfully.", "success")
    return response


@admin_bp.route("/admin")
@login_required
def admin_dashboard():
    """Admin dashboard with statistics."""
    try:
        stats = get_dashboard_stats()
        leave_status = get_leave_status_counts()
        monthly_leave = get_monthly_leave_data()
        department_data = get_department_employee_counts()
        activities = get_recent_activities()

        return render_template(
            "dashboard.html",
            stats=stats,
            leave_status=leave_status,
            monthly_leave=monthly_leave,
            department_data=department_data,
            activities=activities,
        )
    except Exception:
        logger.exception("ADMIN_DASHBOARD_ERROR")
        flash("❌ Error loading dashboard", "danger")
        return "Error loading dashboard", 500


# =====================================================
# EMPLOYEES
# =====================================================

@admin_bp.route("/employees")
@login_required
def employees():
    """List all employees with search."""
    search = request.args.get("search", "").strip()
    employees_list = get_all_employees(search)
    return render_template(
        "employees.html",
        employees=employees_list,
        search=search,
    )


@admin_bp.route("/add-employee", methods=["GET", "POST"])
@login_required
def add_employee_page():
    """Add new employee."""
    if request.method == "POST":
        try:
            employee_id = request.form.get("employee_id", "").strip()
            name = request.form.get("name", "").strip()
            country_code = request.form.get("country_code", "91").strip()
            phone = request.form.get("whatsapp", "").strip()
            manager = request.form.get("manager", "").strip()
            department = request.form.get("department", "").strip()

            if not all([employee_id, name, phone, manager, department]):
                flash("❌ All fields are required", "danger")
                return render_template("add_employee.html")

            whatsapp = validate_phone(country_code, phone)
            add_employee(employee_id, name, whatsapp, manager, department)
            log_activity(f"👤 New employee added: {name} ({employee_id})")

            flash("✅ Employee added successfully!", "success")
            return redirect(url_for("admin.employees"))

        except ValidationError as e:
            flash(f"❌ {str(e)}", "danger")
        except Exception:
            logger.exception("ADD_EMPLOYEE_ERROR")
            flash("❌ Failed to add employee", "danger")

    return render_template("add_employee.html")


@admin_bp.route("/edit-employee/<employee_id>", methods=["GET", "POST"])
@login_required
def edit_employee(employee_id):
    """Edit employee details."""
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            country_code = request.form.get("country_code", "91").strip()
            whatsapp = request.form.get("whatsapp", "").strip()
            manager = request.form.get("manager", "").strip()
            department = request.form.get("department", "").strip()

            if not all([name, whatsapp, manager, department]):
                flash("❌ All fields are required", "danger")
                return render_template(
                    "edit_employee.html",
                    employee=(employee_id, name, whatsapp, manager, department),
                )

            whatsapp = validate_phone(country_code, whatsapp)
            update_employee(employee_id, name, whatsapp, manager, department)
            log_activity(f"✏️ Employee updated: {name} ({employee_id})")

            flash("✅ Employee updated successfully!", "success")
            return redirect(url_for("admin.employees"))

        except ValidationError as e:
            flash(f"❌ {str(e)}", "danger")
        except Exception:
            logger.exception("EDIT_EMPLOYEE_ERROR")
            flash("❌ Failed to update employee", "danger")

    employee = get_employee(employee_id)
    if not employee:
        flash("❌ Employee not found", "danger")
        return redirect(url_for("admin.employees"))

    return render_template("edit_employee.html", employee=employee)


@admin_bp.route("/delete-employee/<employee_id>")
@login_required
def delete_employee_route(employee_id):
    """Delete employee."""
    try:
        employee = get_employee(employee_id)
        if not employee:
            flash("❌ Employee not found", "danger")
            return redirect(url_for("admin.employees"))

        delete_employee(employee_id)
        emp_name = (
            employee[1]
            if isinstance(employee, tuple)
            else employee.get("name", employee_id)
        )
        log_activity(f"🗑️ Employee deleted: {emp_name} ({employee_id})")
        flash("✅ Employee deleted successfully!", "success")
    except Exception:
        logger.exception("DELETE_EMPLOYEE_ERROR")
        flash("❌ Failed to delete employee", "danger")

    return redirect(url_for("admin.employees"))


# =====================================================
# LEAVE MANAGEMENT
# =====================================================

@admin_bp.route("/leave-requests")
@login_required
def leave_requests():
    """List all leave requests."""
    try:
        leaves = get_all_leave_requests() or []
        pending_count = sum(1 for l in leaves if l[7] == "Pending")
        approved_count = sum(1 for l in leaves if l[7] == "Approved")
        active_count = approved_count

        return render_template(
            "leave_requests.html",
            leaves=leaves,
            pending_count=pending_count,
            approved_count=approved_count,
            active_count=active_count,
        )
    except Exception:
        logger.exception("LEAVE_REQUESTS_PAGE_ERROR")
        flash("❌ Failed to load leave requests", "danger")
        return redirect(url_for("admin.admin_dashboard"))


@admin_bp.route("/approve-leave/<int:request_id>")
@login_required
def approve_leave(request_id):
    """Approve leave request."""
    try:
        leave = get_leave_details(request_id)
        if not leave:
            flash("❌ Leave request not found", "danger")
            return redirect(url_for("admin.leave_requests"))

        allowed, message = can_approve_leave(
            leave["employee_id"], leave["leave_type"], leave["leave_days"]
        )
        if not allowed:
            log_activity(f"❌ Leave approval blocked for {leave['name']} ({message})")
            flash(f"❌ Cannot approve: {message}", "danger")
            return redirect(url_for("admin.leave_requests"))

        update_leave_status(request_id, "Approved")
        leave = get_leave_details(request_id)
        log_activity(f"✅ Leave approved for {leave['name']}")

        send_text(
            leave["whatsapp"],
            f"✅ *Leave Approved*\n\n"
            f"• *Employee:* {leave['name']}\n"
            f"• *Type:* {leave['leave_type']}\n"
            f"• *Duration:* {leave['from_date']} to {leave['to_date']} ({leave['leave_days']} days)\n"
            f"• *Approved By:* {leave['manager']}\n"
            f"• *Status:* Approved ✓",
        )
        flash("✅ Leave approved successfully!", "success")

    except Exception:
        logger.exception(f"APPROVE_LEAVE_ERROR: {request_id}")
        flash("❌ Failed to approve leave", "danger")

    return redirect(url_for("admin.leave_requests"))


@admin_bp.route("/reject-leave/<int:request_id>")
@login_required
def reject_leave(request_id):
    """Reject leave request."""
    try:
        update_leave_status(request_id, "Rejected")
        leave = get_leave_details(request_id)

        if leave:
            log_activity(f"❌ Leave rejected for {leave['name']}")
            send_text(
                leave["whatsapp"],
                f"❌ *Leave Rejected*\n\n"
                f"• *Employee:* {leave['name']}\n"
                f"• *Duration:* {leave['from_date']} to {leave['to_date']}\n"
                f"• *Reason:* {leave['reason']}\n"
                f"• *Rejected By:* {leave['manager']}\n"
                f"• *Status:* Rejected ✗",
            )

        flash("✅ Leave rejected successfully!", "success")
    except Exception:
        logger.exception(f"REJECT_LEAVE_ERROR: {request_id}")
        flash("❌ Failed to reject leave", "danger")

    return redirect(url_for("admin.leave_requests"))


# =====================================================
# SALARY SLIPS & BULK PROCESSING
# =====================================================

@admin_bp.route("/upload-salary", methods=["GET", "POST"])
@login_required
def upload_salary():
    """Upload single salary slip with password protection."""
    if request.method == "POST":
        try:
            employee_id = request.form.get("employee_id", "").strip()
            month = request.form.get("month")
            year = int(request.form.get("year"))

            month_map = {
                "January": 1, "February": 2, "March": 3, "April": 4,
                "May": 5, "June": 6, "July": 7, "August": 8,
                "September": 9, "October": 10, "November": 11, "December": 12
            }
            month = int(month) if str(month).isdigit() else month_map.get(month, 1)

            file = request.files.get("salary_pdf")
            if not file or not file.filename.endswith(".pdf"):
                flash("❌ Please upload a valid PDF file", "danger")
                return redirect(url_for("admin.upload_salary"))

            employee = get_employee(employee_id)
            if not employee:
                flash(f"❌ Employee {employee_id} not found", "danger")
                return redirect(url_for("admin.upload_salary"))

            emp_id = employee.get("employee_id") if isinstance(employee, dict) else employee[1]
            phone_num = (
                employee.get("whatsapp") or employee.get("phone")
                if isinstance(employee, dict)
                else employee[5]
            )

            pdf_password = generate_salary_pdf_password(emp_id, phone_num)
            file_bytes = file.read()
            encrypted_stream = protect_pdf_with_password(file_bytes, pdf_password)

            filename = secure_filename(f"{employee_id}_{month}_{year}.pdf")
            s3_key = upload_salary_to_s3(encrypted_stream, filename)
            save_salary_slip(employee_id, month, year, s3_key)

            log_activity(f"💰 Salary slip uploaded for {employee_id} ({month}/{year})")
            flash("✅ Password-protected salary slip uploaded successfully!", "success")
            return redirect(url_for("admin.upload_salary"))

        except Exception:
            logger.exception("UPLOAD_SALARY_ERROR")
            flash("❌ Failed to upload salary slip", "danger")

    employees_list = get_all_employees()
    salary_slips = get_all_salary_slips()
    return render_template(
        "upload_salary.html",
        employees=employees_list,
        salary_slips=salary_slips,
    )


@admin_bp.route("/upload-bulk-salary", methods=["POST"])
@login_required
def bulk_upload_salary():
    """Extracts salary PDFs from a ZIP file and enqueues background processing via RQ."""
    zip_file = request.files.get("salary_zip")

    if not zip_file or not zip_file.filename.lower().endswith(".zip"):
        flash("❌ Only .zip files are allowed for bulk upload", "danger")
        return redirect(url_for("admin.upload_salary"))

    try:
        zip_bytes = zip_file.read()

        # Enqueue background task to RQ
        job = salary_task_queue.enqueue(
            process_bulk_salary_slips_job,
            args=(zip_bytes,),
            job_timeout=600,
        )

        flash(
            f"⏳ Bulk salary processing queued in background! (Job ID: {job.get_id()[:8]})",
            "info",
        )
    except Exception as e:
        logger.exception("BULK_SALARY_ENQUEUE_ERROR")
        flash(f"❌ Failed to queue bulk upload: {str(e)}", "danger")

    return redirect(url_for("admin.upload_salary"))


@admin_bp.route("/view-salary/<int:id>")
@login_required
def view_salary_route(id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM salary_slips WHERE id=%s", (id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        flash("❌ Salary slip not found.", "danger")
        return redirect(url_for("admin.upload_salary"))

    return redirect(generate_presigned_url(row[0]))


@admin_bp.route("/delete-salary/<int:id>")
@login_required
def delete_salary_route(id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM salary_slips WHERE id=%s", (id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        flash("❌ Salary slip not found.", "danger")
        return redirect(url_for("admin.upload_salary"))

    s3_key = row[0]
    delete_file_from_s3(s3_key)
    cursor.execute("DELETE FROM salary_slips WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()

    flash("✅ Salary slip deleted successfully!", "success")
    log_activity(f"Deleted salary slip: {s3_key}")
    return redirect(url_for("admin.upload_salary"))


# =====================================================
# POLICY MANAGEMENT
# =====================================================

@admin_bp.route("/policy-management", methods=["GET", "POST"])
@login_required
def policy_management():
    """Manage HR policies with S3 storage and S3-based FAISS vector sync."""
    POLICY_FOLDER = current_app.config.get("POLICY_FOLDER", "uploads/policies")
    os.makedirs(POLICY_FOLDER, exist_ok=True)

    if request.method == "POST":
        file = request.files.get("policy")
        if not file or not file.filename.lower().endswith(".pdf"):
            flash("❌ Only PDF files allowed", "danger")
            return redirect(url_for("admin.policy_management"))

        temp_filepath = None
        try:
            filename = secure_filename(file.filename)
            temp_filepath = os.path.join(POLICY_FOLDER, filename)
            file.save(temp_filepath)

            file_hash = get_pdf_hash(temp_filepath)
            version = get_pdf_version(filename)

            with open(temp_filepath, "rb") as upload_file:
                s3_key = upload_policy_to_s3(upload_file, filename)

            save_policy_file(
                file_name=filename,
                s3_key=s3_key,
                version=version,
                file_hash=file_hash,
            )

            synced = sync_faiss_from_s3()
            log_activity(f"📚 Policy uploaded to S3: {filename}")

            if synced:
                flash("✅ Policy uploaded and FAISS index synced!", "success")
            else:
                flash("✅ Policy uploaded to S3! Please re-index FAISS via indexer.", "info")

            return redirect(url_for("admin.policy_management"))

        except Exception as e:
            logger.exception(f"UPLOAD_POLICY_ERROR | error={e}")
            flash("❌ Failed to upload policy", "danger")

        finally:
            if temp_filepath and os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError:
                    pass

    policies = get_all_policy_files()
    return render_template("policy_management.html", policies=policies)


@admin_bp.route("/delete-policy/<path:filename>", methods=["GET", "POST"])
@login_required
def delete_policy(filename):
    """Delete policy file from S3, database, and update tracking registry."""
    try:
        s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=f"policies/{filename}")
        delete_policy_file(filename)

        try:
            registry = load_pdf_registry()
            if filename in registry:
                del registry[filename]
                save_pdf_registry(registry)
        except Exception as e:
            logger.warning(f"REGISTRY_UPDATE_SKIPPED | {filename} | {e}")

        log_activity(f"🗑️ Deleted policy: {filename}")
        flash(f"✅ Successfully deleted {filename} from S3 and database.", "success")
    except Exception as e:
        logger.error(f"DELETE_FAILED | file={filename} | error={e}")
        flash(f"❌ Failed to delete {filename}: {str(e)}", "danger")

    return redirect(url_for("admin.policy_management"))


@admin_bp.route("/download-policy/<filename>")
@admin_bp.route("/view-policy/<filename>")
@login_required
def view_policy(filename):
    return redirect(generate_presigned_url(f"policies/{filename}"))


# =====================================================
# VIDEO MANAGEMENT
# =====================================================

@admin_bp.route("/upload-video", methods=["GET", "POST"])
@login_required
def upload_video():
    if request.method == "POST":
        title = request.form.get("title")
        category = request.form.get("category")
        file = request.files.get("video")

        if not file or file.filename == "":
            flash("Select a valid video file", "danger")
            return redirect(request.url)

        filename = secure_filename(file.filename)
        s3_key = upload_video_to_s3(file, filename)
        save_training_video(title=title, category=category, s3_key=s3_key)

        flash("✅ Video uploaded successfully", "success")
        return redirect(url_for("admin.upload_video"))

    videos = get_all_training_videos()
    return render_template("upload_video.html", videos=videos)


@admin_bp.route("/delete-video/<int:id>", methods=["GET", "POST"])
@login_required
def delete_video(id):
    try:
        s3_key = delete_training_video(id)
        if s3_key:
            delete_file_from_s3(s3_key)
            log_activity(f"🗑️ Deleted training video [ID: {id}]")
            flash("✅ Training video deleted successfully!", "success")
        else:
            flash("❌ Video not found.", "danger")
    except Exception as e:
        logger.error(f"DELETE_VIDEO_ERROR | id={id} | error={e}")
        flash("❌ Failed to delete video.", "danger")

    return redirect(url_for("admin.upload_video"))