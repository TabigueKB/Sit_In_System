from flask import Flask, render_template, request, redirect, session, flash, jsonify
import sqlite3
import os
import base64
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "secretkey"

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

    rooms = [('524', 30), ('526', 30), ('528', 30)]
    for r in rooms:
        conn.execute("INSERT OR IGNORE INTO rooms (room_number, capacity) VALUES (?, ?)", r)

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
        "ALTER TABLE users ADD COLUMN profile_pic TEXT",
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
    """
    Saves a profile pic from an uploaded file or base64 string.
    Deletes the old pic if it exists.
    Returns the filename (relative to static/uploads/).
    """
    # Delete old picture if it exists
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
        # Strip the data URL prefix: data:image/jpeg;base64,<data>
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

    # Real counts per purpose for bar chart
    purpose_rows = conn.execute("""
        SELECT purpose, COUNT(*) as count
        FROM sitin_records
        GROUP BY purpose
    """).fetchall()
    purpose_map = {row["purpose"]: row["count"] for row in purpose_rows}
    purposes = ["C Programming", "Java", "C#", "PHP"]
    purpose_counts = [purpose_map.get(p, 0) for p in purposes]

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
        search=search or ""
    )

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

    conn.close()

    return render_template("dashboard.html",
                           user=user,
                           active_sitin=active_sitin,
                           labs=labs,
                           announcements=announcements,
                           remaining_session=remaining_session)

# =========================
# SEARCH STUDENT — no separate template needed
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
    # open students modal automatically via flag
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
# SIT-IN
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
# TIME OUT
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

    # Redirect back to dashboard if admin, otherwise to register/login page
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

    # Block if already sitting in
    existing = conn.execute("""
        SELECT * FROM sitin_records WHERE student_id=? AND status='IN'
    """, (user["student_id"],)).fetchone()

    if existing:
        conn.close()
        return redirect("/dashboard")

    # Block if no sessions left
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

    # Deduct one session
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
# UPDATE PROFILE  (now handles profile picture)
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

    new_pic = old_pic  # default: keep existing

    if remove_pic:
        # Delete old file and clear DB field
        if old_pic:
            old_path = os.path.join(UPLOAD_FOLDER, old_pic)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
        new_pic = None

    elif captured_data:
        # Photo was taken via camera (base64)
        new_pic = save_profile_pic(base64_data=captured_data, old_pic=old_pic)

    elif uploaded_file and uploaded_file.filename:
        # Photo was uploaded from file system
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
# LOGOUT
# =========================
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# =========================
# RUN APP
# =========================
if __name__ == "__main__":
    app.run(debug=True)
