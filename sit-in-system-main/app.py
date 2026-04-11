from flask import Flask, render_template, request, redirect, session, flash, jsonify, send_file
import sqlite3
import os
import base64
import uuid
import io
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from werkzeug.utils import secure_filename
from fpdf import FPDF

app = Flask(__name__)
app.secret_key = "secretkey"

# =========================
# EMAIL CONFIG
# =========================
MAIL_SENDER     = "kervytabigue69@gmail.com"      # ← change to your Gmail
MAIL_PASSWORD   = "sloekdoouxlyzyjj"    # ← Gmail App Password (16-char)
# To get an App Password: Google Account → Security → 2-Step Verification → App Passwords

def send_email_async(to_email, subject, html_body):
    """Send email in a background thread so it doesn't block the request."""
    def _send():
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = MAIL_SENDER
            msg["To"]      = to_email
            msg.attach(MIMEText(html_body, "html"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(MAIL_SENDER, MAIL_PASSWORD)
                server.sendmail(MAIL_SENDER, to_email, msg.as_string())
        except Exception as e:
            print(f"[EMAIL ERROR] Failed to send to {to_email}: {e}")
    threading.Thread(target=_send, daemon=True).start()

def notify_reservation(student_email, student_name, action, res):
    """Build and send the approval/decline email to the student."""
    if not student_email:
        return
    approved = (action == "accept")
    color    = "#28a745" if approved else "#dc3545"
    status   = "APPROVED ✅" if approved else "DECLINED ❌"
    subject  = f"CCS Sit-in: Your reservation has been {status}"
    admin_note_html = (
        f"<p><strong>Admin Note:</strong> {res['admin_note']}</p>"
        if res.get("admin_note") else ""
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;border:1px solid #ddd;border-radius:8px;overflow:hidden;">
      <div style="background:{color};padding:20px;text-align:center;">
        <h2 style="color:white;margin:0;">Reservation {status}</h2>
      </div>
      <div style="padding:30px;">
        <p>Hi <strong>{student_name}</strong>,</p>
        <p>Your sit-in reservation has been <strong style="color:{color};">{status}</strong>.</p>
        <table style="width:100%;border-collapse:collapse;margin:16px 0;">
          <tr style="background:#f8f9fa;">
            <td style="padding:8px 12px;font-weight:bold;width:40%;">Lab</td>
            <td style="padding:8px 12px;">Lab {res['lab']}</td>
          </tr>
          <tr>
            <td style="padding:8px 12px;font-weight:bold;">PC Number</td>
            <td style="padding:8px 12px;">PC {res['pc_number']}</td>
          </tr>
          <tr style="background:#f8f9fa;">
            <td style="padding:8px 12px;font-weight:bold;">Date</td>
            <td style="padding:8px 12px;">{res['date']}</td>
          </tr>
          <tr>
            <td style="padding:8px 12px;font-weight:bold;">Time Slot</td>
            <td style="padding:8px 12px;">{res['time_slot']}</td>
          </tr>
          <tr style="background:#f8f9fa;">
            <td style="padding:8px 12px;font-weight:bold;">Purpose</td>
            <td style="padding:8px 12px;">{res['purpose']}</td>
          </tr>
        </table>
        {admin_note_html}
        {"<p>Please proceed to the lab at your scheduled time. Make sure to bring your student ID.</p>" if approved else "<p>You may submit a new reservation request if needed.</p>"}
        <p style="margin-top:24px;font-size:13px;color:#888;">— CCS Sit-in Monitoring System, University of Cebu</p>
      </div>
    </div>
    """
    send_email_async(student_email, subject, html)

# =========================
# PROFILE PIC CONFIG
# =========================
UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# =========================
# DATABASE CONNECTION
# =========================
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# =========================
# INITIALIZE DATABASE
# =========================
def init_db():
    conn = get_db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT UNIQUE,
        first_name TEXT,
        last_name TEXT,
        middle_name TEXT,
        course TEXT,
        course_level TEXT,
        email TEXT,
        address TEXT,
        password TEXT,
        remaining_session INTEGER DEFAULT 30,
        is_admin INTEGER DEFAULT 0,
        profile_pic TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS rooms(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_number TEXT UNIQUE,
        capacity INTEGER,
        is_occupied INTEGER DEFAULT 0,
        current_user_id INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS sitin_records(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT,
        name TEXT,
        purpose TEXT,
        lab TEXT,
        pc_number INTEGER,
        session INTEGER DEFAULT 30,
        time_in DATETIME DEFAULT CURRENT_TIMESTAMP,
        time_out DATETIME,
        status TEXT DEFAULT 'IN'
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS announcements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ── NEW: Reservations table ──
    conn.execute("""
    CREATE TABLE IF NOT EXISTS reservations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT,
        name TEXT,
        purpose TEXT,
        lab TEXT,
        pc_number INTEGER,
        date TEXT,
        time_slot TEXT,
        status TEXT DEFAULT 'PENDING',
        admin_note TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS pc_availability(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_number TEXT,
        date TEXT,
        time_start TEXT,
        time_end TEXT,
        available_pcs TEXT,
        UNIQUE(room_number, date, time_start, time_end)
    )
    """)

    # ── NEW: Feedback table ──
    conn.execute("""
    CREATE TABLE IF NOT EXISTS feedback(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sitin_id INTEGER UNIQUE,
        student_id TEXT,
        name TEXT,
        lab TEXT,
        purpose TEXT,
        rating INTEGER,
        feedback_text TEXT,
        submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    rooms = [('524', 50), ('525', 50), ('526', 50), ('527', 50), ('528', 50), ('529', 50), ('530', 50), ('530A', 50), ('530B', 50), ('530C', 50)]
    for r in rooms:
        conn.execute("INSERT OR IGNORE INTO rooms (room_number, capacity) VALUES (?, ?)", r)
    conn.execute("UPDATE rooms SET capacity=50 WHERE capacity < 50")

    admin = conn.execute("SELECT * FROM users WHERE student_id='admin'").fetchone()
    if not admin:
        conn.execute("""
        INSERT INTO users (student_id, first_name, last_name, password, is_admin)
        VALUES ('admin','Admin','User','admin123',1)
        """)

    # MIGRATIONS — safely add columns that may not exist in older databases
    migrations = [
        "ALTER TABLE users ADD COLUMN remaining_session INTEGER DEFAULT 30",
        "ALTER TABLE sitin_records ADD COLUMN session INTEGER DEFAULT 30",
        "ALTER TABLE sitin_records ADD COLUMN pc_number INTEGER",
        "ALTER TABLE users ADD COLUMN profile_pic TEXT",
        "ALTER TABLE reservations ADD COLUMN admin_note TEXT",
        "ALTER TABLE reservations ADD COLUMN pc_number INTEGER",
        "ALTER TABLE pc_availability ADD COLUMN time_start TEXT",
        "ALTER TABLE pc_availability ADD COLUMN time_end TEXT",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists, skip

    conn.commit()
    conn.close()

init_db()

# =========================
# HELPER: Save profile picture
# =========================
def save_profile_pic(file=None, base64_data=None, old_pic=None):
    if old_pic:
        old_path = os.path.join(UPLOAD_FOLDER, old_pic)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

    filename = None

    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit(".", 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(UPLOAD_FOLDER, filename))

    elif base64_data and base64_data.startswith("data:image"):
        try:
            header, encoded = base64_data.split(",", 1)
            ext = header.split("/")[1].split(";")[0]
            if ext not in ALLOWED_EXTENSIONS:
                ext = "jpg"
            filename = f"{uuid.uuid4().hex}.{ext}"
            img_bytes = base64.b64decode(encoded)
            with open(os.path.join(UPLOAD_FOLDER, filename), "wb") as f:
                f.write(img_bytes)
        except Exception:
            filename = None

    return filename

# =========================
# HELPER: parse and format PC availability selections
# =========================
def parse_pc_selection(text):
    if not text:
        return []

    pcs = set()
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            bounds = part.split('-')
            if len(bounds) == 2 and bounds[0].strip().isdigit() and bounds[1].strip().isdigit():
                start = int(bounds[0].strip())
                end = int(bounds[1].strip())
                if start <= end:
                    for n in range(max(1, start), min(50, end) + 1):
                        pcs.add(n)
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= 50:
                pcs.add(n)

    return sorted(pcs)


def format_pc_selection(pcs):
    if not pcs:
        return ""
    values = sorted({int(x) for x in pcs if str(x).strip().isdigit()})
    ranges = []
    start = prev = values[0]
    for n in values[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = n
    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(ranges)


def get_available_pcs_for_slot(conn, room, date, time_slot):
    # Find all availability rows for this room+date, then filter by time if time_slot given
    rows = conn.execute(
        "SELECT * FROM pc_availability WHERE room_number=? AND date=?",
        (room, date)
    ).fetchall()

    # Pick the row whose time window covers the requested time_slot
    matched_row = None
    if time_slot and rows:
        for row in rows:
            ts = row["time_start"] or ""
            te = row["time_end"] or ""
            if ts and te and ts <= time_slot <= te:
                matched_row = row
                break
        if not matched_row:
            # Fall back to a row with no time restriction
            for row in rows:
                if not row["time_start"] and not row["time_end"]:
                    matched_row = row
                    break
    elif rows:
        # No time_slot requested — use first matching row
        matched_row = rows[0]

    if matched_row and matched_row["available_pcs"]:
        available = parse_pc_selection(matched_row["available_pcs"])
    else:
        available = list(range(1, 51))

    if time_slot:
        blocked_rows = conn.execute(
            """
            SELECT pc_number FROM reservations
            WHERE lab=? AND date=? AND time_slot=? AND status IN ('PENDING','APPROVED')
            """,
            (room, date, time_slot)
        ).fetchall()
        blocked_pcs = {r["pc_number"] for r in blocked_rows if r["pc_number"] is not None}
        available = [n for n in available if n not in blocked_pcs]

    return available


# =========================
# HELPER: load admin dashboard data
# =========================
def get_admin_data(search=None):
    conn = get_db()

    total_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=0").fetchone()[0]

    if search:
        students = conn.execute("""
            SELECT * FROM users
            WHERE is_admin=0 AND (
                student_id LIKE ? OR
                first_name LIKE ? OR
                last_name LIKE ? OR
                middle_name LIKE ?
            )
        """, (f"%{search}%",) * 4).fetchall()
    else:
        students = conn.execute("SELECT * FROM users WHERE is_admin=0").fetchall()

    sitin_records = conn.execute(
        "SELECT * FROM sitin_records ORDER BY time_in DESC"
    ).fetchall()

    current_sitin_count = conn.execute(
        "SELECT COUNT(*) FROM sitin_records WHERE status='IN'"
    ).fetchone()[0]

    total_sitin_count = conn.execute(
        "SELECT COUNT(*) FROM sitin_records"
    ).fetchone()[0]

    announcements = conn.execute(
        "SELECT * FROM announcements ORDER BY id DESC"
    ).fetchall()

    rooms = conn.execute("SELECT * FROM rooms ORDER BY room_number").fetchall()

    purpose_rows = conn.execute("""
        SELECT purpose, COUNT(*) as count
        FROM sitin_records
        GROUP BY purpose
    """).fetchall()
    purpose_map = {row["purpose"]: row["count"] for row in purpose_rows}
    purposes = ["C Programming", "Java", "C#", "PHP"]
    purpose_counts = [purpose_map.get(p, 0) for p in purposes]

    # ── Reservations ──
    reservations = conn.execute("""
        SELECT * FROM reservations ORDER BY created_at DESC
    """).fetchall()

    pending_reservations_count = conn.execute(
        "SELECT COUNT(*) FROM reservations WHERE status='PENDING'"
    ).fetchone()[0]

    # ── Feedback reports ──
    feedback_reports = conn.execute("""
        SELECT f.*, s.time_in, s.time_out
        FROM feedback f
        LEFT JOIN sitin_records s ON s.id = f.sitin_id
        ORDER BY f.submitted_at DESC
    """).fetchall()

    avg_rating = conn.execute(
        "SELECT ROUND(AVG(rating), 2) FROM feedback"
    ).fetchone()[0] or 0

    pc_availabilities = conn.execute(
        "SELECT * FROM pc_availability ORDER BY date DESC, room_number"
    ).fetchall()

    conn.close()

    return dict(
        total_users=total_users,
        students=students,
        sitin_records=sitin_records,
        current_sitin_count=current_sitin_count,
        total_sitin_count=total_sitin_count,
        announcements=announcements,
        rooms=rooms,
        purposes=purposes,
        purpose_counts=purpose_counts,
        search=search or "",
        reservations=reservations,
        pending_reservations_count=pending_reservations_count,
        feedback_reports=feedback_reports,
        avg_rating=avg_rating,
        pc_availabilities=pc_availabilities,
    )

@app.route("/available_pcs")
def available_pcs():
    room = request.args.get("room", "").strip()
    date = request.args.get("date", "").strip()
    time_slot = request.args.get("time_slot", "").strip()

    if not room or not date:
        return jsonify({"pcs": []})

    conn = get_db()
    available = get_available_pcs_for_slot(conn, room, date, time_slot)
    conn.close()
    return jsonify({"pcs": available})


@app.route("/save_pc_availability", methods=["POST"])
def save_pc_availability():
    if not session.get("is_admin"):
        return redirect("/")

    room = request.form.get("room_number", "").strip()
    date = request.form.get("availability_date", "").strip()
    time_start = request.form.get("time_start", "").strip()
    time_end = request.form.get("time_end", "").strip()
    available_pcs_text = request.form.get("available_pcs", "").strip()

    if not room or not date:
        flash("Room and date are required.", "warning")
        return redirect("/dashboard")

    pcs = parse_pc_selection(available_pcs_text)
    if not pcs:
        flash("Enter valid PC numbers between 1 and 50.", "warning")
        return redirect("/dashboard")

    formatted = format_pc_selection(pcs)
    conn = get_db()
    conn.execute(
        """
        INSERT INTO pc_availability (room_number, date, time_start, time_end, available_pcs)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(room_number, date, time_start, time_end) DO UPDATE SET available_pcs=excluded.available_pcs
        """,
        (room, date, time_start or None, time_end or None, formatted)
    )
    conn.commit()
    conn.close()

    time_label = f" from {time_start} to {time_end}" if time_start and time_end else ""
    flash(f"PC availability saved for Lab {room} on {date}{time_label}.", "success")
    return redirect("/dashboard")


# =========================
# LOGIN
# =========================
@app.route("/")
def login():
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login_user():
    student_id = request.form["student_id"]
    password = request.form["password"]

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE student_id=? AND password=?",
        (student_id, password)
    ).fetchone()
    conn.close()

    if user:
        session["user_id"] = user["id"]
        session["is_admin"] = user["is_admin"]
        return redirect("/dashboard")

    return "<script>alert('Invalid login');window.location='/'</script>"

# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/")

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()

    if session.get("is_admin"):
        data = get_admin_data()
        return render_template("admin_dashboard.html", user=user, open_search=False, **data)

    # STUDENT VIEW
    conn = get_db()

    active_sitin = conn.execute(
        "SELECT * FROM sitin_records WHERE student_id=? AND status='IN'",
        (user["student_id"],)
    ).fetchone()

    lab_counts = conn.execute(
        "SELECT lab, COUNT(*) as count FROM sitin_records WHERE status='IN' GROUP BY lab"
    ).fetchall()
    lab_count_map = {row["lab"]: row["count"] for row in lab_counts}

    all_rooms = conn.execute("SELECT * FROM rooms ORDER BY room_number").fetchall()
    labs = [
        {"name": r["room_number"], "capacity": r["capacity"], "current": lab_count_map.get(r["room_number"], 0)}
        for r in all_rooms
    ]

    announcements = conn.execute(
        "SELECT * FROM announcements ORDER BY id DESC"
    ).fetchall()

    student_data = conn.execute(
        "SELECT remaining_session FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    remaining_session = student_data["remaining_session"] if student_data else 30

    # ── Student's own reservations ──
    my_reservations = conn.execute("""
        SELECT * FROM reservations
        WHERE student_id=?
        ORDER BY created_at DESC
    """, (user["student_id"],)).fetchall()

    # Check if student already has a pending reservation
    pending_reservation = conn.execute("""
        SELECT * FROM reservations
        WHERE student_id=? AND status='PENDING'
        LIMIT 1
    """, (user["student_id"],)).fetchone()

    # ── Recent sit-in sessions with has_feedback flag ──
    recent_rows = conn.execute("""
        SELECT s.*,
               CASE WHEN f.id IS NOT NULL THEN 1 ELSE 0 END AS has_feedback
        FROM sitin_records s
        LEFT JOIN feedback f ON f.sitin_id = s.id
        WHERE s.student_id=?
        ORDER BY s.time_in DESC
        LIMIT 20
    """, (user["student_id"],)).fetchall()

    conn.close()

    return render_template("dashboard.html",
                           user=user,
                           active_sitin=active_sitin,
                           labs=labs,
                           announcements=announcements,
                           remaining_session=remaining_session,
                           my_reservations=my_reservations,
                           pending_reservation=pending_reservation,
                           recent_sessions=recent_rows)

# =========================
# SEARCH STUDENT
# =========================
@app.route("/search_student")
def search_student():
    if "user_id" not in session or not session.get("is_admin"):
        return redirect("/")

    search = request.args.get("search", "").strip()
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()

    data = get_admin_data(search=search)
    return render_template("admin_dashboard.html", user=user, open_search=True, **data)

# =========================
# GET STUDENT INFO (sit-in modal autocomplete)
# =========================
@app.route("/get_student_info")
def get_student_info():
    id_number = request.args.get("id")
    conn = get_db()
    student = conn.execute("""
        SELECT first_name, last_name, remaining_session
        FROM users WHERE student_id=? AND is_admin=0
    """, (id_number,)).fetchone()
    conn.close()

    if student:
        return jsonify({
            "success": True,
            "name": f"{student['first_name']} {student['last_name']}",
            "remaining_session": student['remaining_session'] or 30
        })
    return jsonify({"success": False})

# =========================
# SIT-IN (admin)
# =========================
@app.route("/sitin", methods=["POST"])
def sitin():
    id_number = request.form["id_number"]
    name = request.form["student_name"]
    purpose = request.form["purpose"]
    lab = request.form["lab"]

    conn = get_db()

    existing = conn.execute("""
        SELECT * FROM sitin_records WHERE student_id=? AND status='IN'
    """, (id_number,)).fetchone()

    if existing:
        flash("Student is already sitting in!", "error")
        conn.close()
        return redirect("/dashboard")

    student = conn.execute(
        "SELECT remaining_session FROM users WHERE student_id=?", (id_number,)
    ).fetchone()
    remaining = student['remaining_session'] if student else 30

    conn.execute("""
        INSERT INTO sitin_records (student_id, name, purpose, lab, session)
        VALUES (?, ?, ?, ?, ?)
    """, (id_number, name, purpose, lab, remaining))
    conn.commit()
    conn.close()
    flash("Sit-in recorded successfully!", "success")
    return redirect("/dashboard")

# =========================
# TIME OUT (admin)
# =========================
@app.route("/timeout/<int:id>", methods=["POST"])
def timeout(id):
    conn = get_db()
    conn.execute("""
        UPDATE sitin_records SET status='OUT', time_out=CURRENT_TIMESTAMP WHERE id=?
    """, (id,))
    conn.commit()
    conn.close()
    flash("Student timed out successfully!", "success")
    return redirect("/dashboard")

# =========================
# REGISTER
# =========================
@app.route("/register")
def register():
    return render_template("register.html")

@app.route("/register_user", methods=["POST"])
def register_user():
    data = (
        request.form["student_id"],
        request.form["first_name"],
        request.form["last_name"],
        request.form.get("middle_name", ""),
        request.form["course"],
        request.form["course_level"],
        request.form.get("email", ""),
        request.form.get("address", ""),
        request.form["password"]
    )

    is_admin = session.get("is_admin", False)
    redirect_on_error   = "/dashboard" if is_admin else "/register"
    redirect_on_success = "/dashboard" if is_admin else "/"

    conn = get_db()
    existing = conn.execute("SELECT * FROM users WHERE student_id=?", (data[0],)).fetchone()
    if existing:
        conn.close()
        flash("Student ID already exists.", "error")
        return redirect(redirect_on_error)

    conn.execute("""
        INSERT INTO users
        (student_id, first_name, last_name, middle_name, course, course_level, email, address, password)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, data)
    conn.commit()
    conn.close()
    flash("Student registered successfully!", "success")
    return redirect(redirect_on_success)

# =========================
# EDIT STUDENT
# =========================
@app.route("/edit_student", methods=["POST"])
def edit_student():
    if not session.get("is_admin"):
        return redirect("/")

    conn = get_db()
    conn.execute("""
        UPDATE users SET first_name=?, last_name=?, course=?, course_level=?, email=?
        WHERE student_id=?
    """, (
        request.form["first_name"],
        request.form["last_name"],
        request.form["course"],
        request.form["course_level"],
        request.form.get("email", ""),
        request.form["student_id"]
    ))
    conn.commit()
    conn.close()
    flash("Student updated successfully!", "success")
    return redirect("/dashboard")

# =========================
# DELETE STUDENT
# =========================
@app.route("/delete_student/<student_id>", methods=["POST"])
def delete_student(student_id):
    if not session.get("is_admin"):
        return redirect("/")
    conn = get_db()
    conn.execute("DELETE FROM users WHERE student_id=? AND is_admin=0", (student_id,))
    conn.commit()
    conn.close()
    flash("Student deleted successfully!", "success")
    return redirect("/dashboard")

# =========================
# RESET ALL SESSIONS
# =========================
@app.route("/reset_all_sessions", methods=["POST"])
def reset_all_sessions():
    if not session.get("is_admin"):
        return redirect("/")
    conn = get_db()
    conn.execute("UPDATE users SET remaining_session=30 WHERE is_admin=0")
    conn.commit()
    conn.close()
    flash("All sessions have been reset!", "success")
    return redirect("/dashboard")

# =========================
# POST ANNOUNCEMENT
# =========================
@app.route("/post_announcement", methods=["POST"])
def post_announcement():
    if not session.get("is_admin"):
        return redirect("/")
    text = request.form["announcement"]
    conn = get_db()
    conn.execute("INSERT INTO announcements (text) VALUES (?)", (text,))
    conn.commit()
    conn.close()
    flash("Announcement posted successfully!", "success")
    return redirect("/dashboard")

# =========================
# STUDENT SELF SIT-IN
# =========================
@app.route("/student_sitin", methods=["POST"])
def student_sitin():
    if "user_id" not in session or session.get("is_admin"):
        return redirect("/")

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

    existing = conn.execute("""
        SELECT * FROM sitin_records WHERE student_id=? AND status='IN'
    """, (user["student_id"],)).fetchone()

    if existing:
        conn.close()
        return redirect("/dashboard")

    if user["remaining_session"] <= 0:
        conn.close()
        return redirect("/dashboard")

    lab = request.form["lab"]
    purpose = request.form["purpose"]
    name = f"{user['first_name']} {user['last_name']}"

    conn.execute("""
        INSERT INTO sitin_records (student_id, name, purpose, lab, session)
        VALUES (?, ?, ?, ?, ?)
    """, (user["student_id"], name, purpose, lab, user["remaining_session"]))

    conn.execute("""
        UPDATE users SET remaining_session = remaining_session - 1 WHERE id=?
    """, (session["user_id"],))

    conn.commit()
    conn.close()
    flash("Sit-in recorded successfully!", "success")
    return redirect("/dashboard")

# =========================
# STUDENT SELF TIME OUT
# =========================
@app.route("/student_timeout/<int:id>", methods=["POST"])
def student_timeout(id):
    if "user_id" not in session or session.get("is_admin"):
        return redirect("/")

    conn = get_db()
    conn.execute("""
        UPDATE sitin_records SET status='OUT', time_out=CURRENT_TIMESTAMP WHERE id=?
    """, (id,))
    conn.commit()
    conn.close()
    flash("Timed out successfully!", "success")
    return redirect("/dashboard")

# =========================
# UPDATE PROFILE
# =========================
@app.route("/update_profile", methods=["POST"])
def update_profile():
    if "user_id" not in session:
        return redirect("/")

    first_name    = request.form["first_name"]
    last_name     = request.form["last_name"]
    email         = request.form["email"]
    address       = request.form["address"]
    remove_pic    = request.form.get("remove_pic", "0") == "1"
    captured_data = request.form.get("captured_photo", "").strip()
    uploaded_file = request.files.get("profile_pic")

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    old_pic = user["profile_pic"] if user else None

    new_pic = old_pic

    if remove_pic:
        if old_pic:
            old_path = os.path.join(UPLOAD_FOLDER, old_pic)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
        new_pic = None

    elif captured_data:
        new_pic = save_profile_pic(base64_data=captured_data, old_pic=old_pic)

    elif uploaded_file and uploaded_file.filename:
        new_pic = save_profile_pic(file=uploaded_file, old_pic=old_pic)

    conn.execute("""
        UPDATE users
        SET first_name=?, last_name=?, email=?, address=?, profile_pic=?
        WHERE id=?
    """, (first_name, last_name, email, address, new_pic, session["user_id"]))
    conn.commit()
    conn.close()
    flash("Profile updated successfully!", "success")
    return redirect("/dashboard")

# =========================
# ── RESERVATIONS ──
# =========================

@app.route("/reserve_sitin", methods=["POST"])
def reserve_sitin():
    """Student submits a reservation request."""
    if "user_id" not in session or session.get("is_admin"):
        return redirect("/")

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

    # Block if already has a pending reservation
    existing_pending = conn.execute("""
        SELECT * FROM reservations WHERE student_id=? AND status='PENDING'
    """, (user["student_id"],)).fetchone()

    if existing_pending:
        flash("You already have a pending reservation. Please wait for admin approval.", "warning")
        conn.close()
        return redirect("/dashboard")

    # Block if already sitting in
    active_sitin = conn.execute("""
        SELECT * FROM sitin_records WHERE student_id=? AND status='IN'
    """, (user["student_id"],)).fetchone()

    if active_sitin:
        flash("You are currently sitting in. Time out first before making a reservation.", "warning")
        conn.close()
        return redirect("/dashboard")

    if user["remaining_session"] <= 0:
        flash("You have no remaining sessions.", "error")
        conn.close()
        return redirect("/dashboard")

    lab        = request.form["lab"]
    pc_number  = request.form.get("pc_number", "").strip()
    purpose    = request.form["purpose"]
    date       = request.form["date"]
    time_slot  = request.form["time_slot"]
    name       = f"{user['first_name']} {user['last_name']}"

    if not pc_number.isdigit():
        flash("Please select a valid PC number.", "warning")
        conn.close()
        return redirect("/dashboard")

    pc_number = int(pc_number)
    if pc_number < 1 or pc_number > 50:
        flash("PC number must be between 1 and 50.", "warning")
        conn.close()
        return redirect("/dashboard")

    available = get_available_pcs_for_slot(conn, lab, date, time_slot)
    if pc_number not in available:
        flash("That PC is not available for the selected lab/date/time.", "warning")
        conn.close()
        return redirect("/dashboard")

    duplicate = conn.execute("""
        SELECT 1 FROM reservations
        WHERE lab=? AND date=? AND time_slot=? AND pc_number=? AND status IN ('PENDING','APPROVED')
    """, (lab, date, time_slot, pc_number)).fetchone()
    if duplicate:
        flash("That PC is already reserved for the selected slot.", "warning")
        conn.close()
        return redirect("/dashboard")

    conn.execute("""
        INSERT INTO reservations (student_id, name, purpose, lab, pc_number, date, time_slot)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user["student_id"], name, purpose, lab, pc_number, date, time_slot))
    conn.commit()
    conn.close()
    flash("Reservation submitted! Please wait for admin approval.", "success")
    return redirect("/dashboard")


@app.route("/cancel_reservation/<int:res_id>", methods=["POST"])
def cancel_reservation(res_id):
    """Student cancels their own pending reservation."""
    if "user_id" not in session or session.get("is_admin"):
        return redirect("/")

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.execute("""
        UPDATE reservations SET status='CANCELLED'
        WHERE id=? AND student_id=? AND status='PENDING'
    """, (res_id, user["student_id"]))
    conn.commit()
    conn.close()
    flash("Reservation cancelled.", "info")
    return redirect("/dashboard")


@app.route("/admin_reservation_action/<int:res_id>/<action>", methods=["POST"])
def admin_reservation_action(res_id, action):
    """Admin accepts or declines a reservation."""
    if not session.get("is_admin"):
        return redirect("/")

    if action not in ("accept", "decline"):
        return redirect("/dashboard")

    admin_note = request.form.get("admin_note", "").strip()
    new_status = "APPROVED" if action == "accept" else "DECLINED"

    conn = get_db()

    if action == "accept":
        # Fetch reservation details to auto-create a sit-in record
        res = conn.execute("SELECT * FROM reservations WHERE id=?", (res_id,)).fetchone()
        if res and res["status"] == "PENDING":
            # Check student still has sessions
            student = conn.execute(
                "SELECT * FROM users WHERE student_id=?", (res["student_id"],)
            ).fetchone()

            # Check not already sitting in
            existing = conn.execute("""
                SELECT * FROM sitin_records WHERE student_id=? AND status='IN'
            """, (res["student_id"],)).fetchone()

            if existing:
                flash("Student is already sitting in — cannot approve reservation.", "error")
                conn.close()
                return redirect("/dashboard")

            remaining = student["remaining_session"] if student else 30

            conn.execute("""
                INSERT INTO sitin_records (student_id, name, purpose, lab, pc_number, session)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (res["student_id"], res["name"], res["purpose"], res["lab"], res["pc_number"], remaining))

            # Deduct one session
            conn.execute("""
                UPDATE users SET remaining_session = remaining_session - 1
                WHERE student_id=?
            """, (res["student_id"],))

    conn.execute("""
        UPDATE reservations SET status=?, admin_note=? WHERE id=?
    """, (new_status, admin_note, res_id))

    conn.commit()

    # ── Send email notification to the student ──
    try:
        res_row = conn.execute("SELECT * FROM reservations WHERE id=?", (res_id,)).fetchone()
        if res_row:
            student = conn.execute(
                "SELECT email, first_name, last_name FROM users WHERE student_id=?",
                (res_row["student_id"],)
            ).fetchone()
            if student and student["email"]:
                # Merge admin_note into res dict for the email template
                res_data = dict(res_row)
                res_data["admin_note"] = admin_note
                notify_reservation(
                    student["email"],
                    f"{student['first_name']} {student['last_name']}",
                    action,
                    res_data
                )
    except Exception as e:
        print(f"[EMAIL NOTIFY ERROR] {e}")

    conn.close()

    msg = "Reservation approved — sit-in created!" if action == "accept" else "Reservation declined."
    flash(msg, "success" if action == "accept" else "warning")
    return redirect("/dashboard")


# =========================
# SUBMIT FEEDBACK
# =========================
@app.route("/submit_feedback", methods=["POST"])
def submit_feedback():
    if "user_id" not in session or session.get("is_admin"):
        return redirect("/")

    sitin_id    = request.form.get("sitin_id", "").strip()
    rating      = request.form.get("rating", "").strip()
    feedback_text = request.form.get("feedback_text", "").strip()

    if not sitin_id or not rating or not feedback_text:
        flash("Please complete the feedback form.", "warning")
        return redirect("/dashboard")

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

    # Make sure the sitin record belongs to this student and is finished
    record = conn.execute("""
        SELECT * FROM sitin_records
        WHERE id=? AND student_id=? AND status='OUT'
    """, (sitin_id, user["student_id"])).fetchone()

    if not record:
        flash("Cannot submit feedback for this session.", "error")
        conn.close()
        return redirect("/dashboard")

    # Prevent duplicate feedback
    existing = conn.execute(
        "SELECT id FROM feedback WHERE sitin_id=?", (sitin_id,)
    ).fetchone()

    if existing:
        flash("Feedback already submitted for this session.", "warning")
        conn.close()
        return redirect("/dashboard")

    name = f"{user['first_name']} {user['last_name']}"
    conn.execute("""
        INSERT INTO feedback (sitin_id, student_id, name, lab, purpose, rating, feedback_text)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (sitin_id, user["student_id"], name, record["lab"], record["purpose"],
          int(rating), feedback_text))
    conn.commit()
    conn.close()
    flash("Thank you for your feedback!", "success")
    return redirect("/dashboard")


# =========================
# LOGOUT
# =========================
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/export_sitin_report")
def export_sitin_report():
    if not session.get("is_admin"):
        return redirect("/")

    conn = get_db()
    sitin_records = conn.execute(
        "SELECT * FROM sitin_records ORDER BY time_in DESC"
    ).fetchall()
    conn.close()

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "University of Cebu Sit-in Report", ln=1, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 8, datetime.now().strftime("Generated: %B %d, %Y %I:%M %p"), ln=1, align="C")
    pdf.ln(4)

    headers = ["#", "Student ID", "Name", "Purpose", "Lab", "Session", "Time In", "Time Out", "Duration", "Status"]
    col_widths = [12, 28, 48, 30, 24, 20, 38, 38, 28, 24]
    line_height = 8

    pdf.set_fill_color(94, 58, 140)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", "B", 9)
    for idx, header in enumerate(headers):
        pdf.cell(col_widths[idx], line_height, header, border=1, align="C", fill=True)
    pdf.ln(line_height)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "", 8)

    for index, record in enumerate(sitin_records, start=1):
        student_id = record[1]
        name = record[2] or ""
        purpose = record[3] or ""
        lab = record[4] or ""
        session_count = str(record[5]) if record[5] is not None else ""
        time_in = record[6] or ""
        time_out = record[7] or ""
        status = record[8] or ""

        duration = ""
        if time_in and time_out:
            try:
                dt_in = datetime.fromisoformat(time_in)
                dt_out = datetime.fromisoformat(time_out)
                duration = str(dt_out - dt_in).split(".")[0]
            except Exception:
                duration = ""

        row_values = [
            str(index), student_id, name, purpose, lab,
            session_count, time_in, time_out, duration, status
        ]

        for idx, value in enumerate(row_values):
            text = str(value)
            if len(text) > 30:
                text = text[:27] + "..."
            pdf.cell(col_widths[idx], line_height, text, border=1)
        pdf.ln(line_height)

    pdf_bytes = bytes(pdf.output(dest='S'))
    output = io.BytesIO(pdf_bytes)
    output.seek(0)

    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="university_of_cebu_sit_in_report.pdf"
    )

# =========================
# RUN APP
# =========================
if __name__ == "__main__":
    app.run(debug=True)
