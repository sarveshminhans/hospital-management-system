from flask import Flask, request, redirect, url_for, session, render_template, flash, jsonify
from datetime import date, timedelta, datetime
import json
import sqlite3
import hashlib
import os
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()


app = Flask(__name__)
app.secret_key = 'a_very_secret_and_complex_key_for_hospital_app'
DATABASE_NAME = 'hospital.db'

# ===================== AI CONFIG (GEMMA 3N) =====================
print("API KEY FOUND:", os.getenv("OPENROUTER_API_KEY") is not None)

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "http://localhost:5000",
        "X-Title": "Hospital Management AI"
    }
)

# ===============================================================

def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# ===================== DATABASE INIT =====================
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            user_id INTEGER UNIQUE,
            department TEXT,
            department_id TEXT NOT NULL,
            experience INTEGER,
            blacklisted INTEGER NOT NULL DEFAULT 1
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            user_id INTEGER UNIQUE,
            blacklisted INTEGER NOT NULL DEFAULT 1
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            department_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            doctors_registered INTEGER DEFAULT 0
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY,
            patient_name TEXT,
            patient_id INTEGER,
            doctor_name TEXT,
            doctor_id INTEGER,
            date TEXT,
            slot TEXT,
            status TEXT DEFAULT 'confirmed',
            department TEXT,
            sr_no INTEGER,
            created_at TEXT
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY,
            patient_name TEXT,
            visit_no INTEGER,
            visit_type TEXT,
            tests_done TEXT,
            diagnosis TEXT,
            prescription TEXT,
            medicines TEXT
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS doctor_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER,
            date TEXT,
            slot TEXT,
            status INTEGER DEFAULT 1,
            UNIQUE(doctor_id, date, slot)
        );
    """)

    if cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ('admin@123', hash_password('admin@123'), 'admin')
        )

    conn.commit()
    conn.close()


def authenticate_user(username, password):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if user and user['password_hash'] == hash_password(password):
        return dict(user)
    return None


def fetch_admin_dashboard_data():
    conn = get_db_connection()
    users = conn.execute("SELECT id, username, role FROM users").fetchall()
    doctors = conn.execute("SELECT id, name, user_id, department, experience, blacklisted FROM doctors").fetchall()
    patients = conn.execute("SELECT id, name, user_id, blacklisted FROM patients").fetchall()
    appointments = conn.execute("SELECT sr_no, patient_name, doctor_name, status, department FROM appointments").fetchall()
    departments = conn.execute("SELECT department_id, name, doctors_registered FROM departments").fetchall()
    conn.close()

    return {
        'users': [dict(u) for u in users],
        'doctors': [dict(d) for d in doctors],
        'patients': [dict(p) for p in patients],
        'appointments': [dict(a) for a in appointments],
        'departments': [dict(de) for de in departments]
    }


def fetch_doctor_dashboard_data(doctor_name):
    conn = get_db_connection()
    appointments = conn.execute(
        "SELECT sr_no, patient_name, doctor_name, department FROM appointments WHERE doctor_name = ?",
        (doctor_name,)
    ).fetchall()
    patients = conn.execute("SELECT name FROM patients").fetchall()
    conn.close()

    return {
        'name': doctor_name,
        'appointments': [dict(a) for a in appointments],
        'patients': [dict(p) for p in patients]
    }


def fetch_patient_dashboard_data(patient_name):
    conn = get_db_connection()
    appointments = conn.execute(
        "SELECT sr_no, patient_name, doctor_name, department FROM appointments WHERE patient_name = ?",
        (patient_name,)
    ).fetchall()
    departments = conn.execute("SELECT department_id, name, doctors_registered FROM departments").fetchall()
    users = conn.execute("SELECT name FROM patients WHERE username = ?", (patient_name,)).fetchone()
    last_visit = conn.execute("""
        SELECT visit_type, diagnosis, prescription
        FROM visits
        WHERE patient_name = ?
        ORDER BY visit_no DESC LIMIT 1
    """, (patient_name,)).fetchone()

    conn.close()

    return {
        'name': users['name'],
        'username': patient_name,
        'appointments': [dict(a) for a in appointments],
        'last_visit': dict(last_visit) if last_visit else None,
        'departments': [dict(de) for de in departments]
    }


def fetch_patient_history(patient_name):
    conn = get_db_connection()
    patient_info = {
        'name': patient_name,
        'doctor_name': 'Dr. Alice Smith',
        'department': 'Cardiology'
    }
    visits = conn.execute("""
        SELECT visit_no, visit_type, tests_done, diagnosis, prescription, medicines
        FROM visits WHERE patient_name = ?
        ORDER BY visit_no DESC
    """, (patient_name,)).fetchall()
    conn.close()

    patient_info['visits'] = [dict(v) for v in visits]
    return patient_info


@app.route("/", methods=["GET"])
def home():
    return render_template("home.html")
@app.route("/ai/chat", methods=["POST"])
def ai_chat():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Login required"}), 401

    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"success": False, "error": "Message is required"}), 400

    try:
        response = client.chat.completions.create(
            model="google/gemma-2-9b-it:free",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a medical assistant inside a hospital system. "
                        "Give general health advice only. "
                        "Do not diagnose or prescribe medicines."
                    )
                },
                {"role": "user", "content": message}
            ],
            temperature=0.6,
            max_tokens=300
        )

        # 🔒 SAFETY CHECK
        if not response or not response.choices:
            raise Exception("Empty or invalid response from OpenRouter")

        reply = response.choices[0].message.content.strip()

        return jsonify({"success": True, "reply": reply})

    except Exception as e:
        print("OPENROUTER ERROR:", repr(e))
        return jsonify({
            "success": False,
            "error": "AI service temporarily unavailable"
        }), 502


@app.route("/ai_assistant")
def ai_assistant():
    # 🔐 Only logged-in patients can access
    if "user_id" not in session or session.get("user_role") != "patient":
        return redirect(url_for("login"))

    return render_template("patientai.html")

@app.route("/home.html")
def redirect_to_home():
    return redirect(url_for('home'))


@app.route("/login.html", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = authenticate_user(username, password)

        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['user_role'] = user['role']

            if user['role'] == 'admin':
                return redirect(url_for('admin_home'))

            elif user['role'] == 'doctor':
                conn = get_db_connection()
                doctor = conn.execute("SELECT name FROM doctors WHERE user_id = ?", (user['id'],)).fetchone()
                conn.close()
                if doctor:
                    session['doctor_name'] = doctor['name']
                return redirect(url_for('doctor_home'))

            elif user['role'] == 'patient':
                session['patient_name'] = user['username']
                return redirect(url_for('patienthome'))

        return render_template("login.html", error="Invalid username or password.")

    return render_template("login.html")


@app.route("/register.html", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        new_username = request.form.get("username")
        new_password = request.form.get("password")
        new_name = request.form.get("name")

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (new_username, hash_password(new_password), 'patient')
            )
            new_user_id = cursor.lastrowid
            cursor.execute("INSERT INTO patients (username, name, user_id) VALUES (?, ?, ?)",
                           (new_username, new_name, new_user_id))
            conn.commit()
            return redirect(url_for('login'))

        except sqlite3.IntegrityError:
            conn.close()
            return render_template("register.html", error="Username already exists.")

        finally:
            conn.close()

    return render_template("register.html")


@app.route("/search")
def search():
    q = request.args.get("q", "")
    conn = get_db_connection()

    doctors = conn.execute(
        "SELECT name, department, experience FROM doctors WHERE name LIKE ? OR department LIKE ?",
        (f"%{q}%", f"%{q}%")
    ).fetchall()

    patients = conn.execute(
        "SELECT name FROM patients WHERE name LIKE ?",
        (f"%{q}%",)
    ).fetchall()

    departments = conn.execute(
        "SELECT name, description FROM departments WHERE name LIKE ?",
        (f"%{q}%",)
    ).fetchall()

    conn.close()

    return render_template(
        "search_results.html",
        q=q,
        doctors=doctors,
        patients=patients,
        departments=departments
    )


@app.route("/adddoctor.html", methods=["GET", "POST"])
def add_doctor():
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    if request.method == "POST":
        fullname = request.form.get("fullname")
        username = request.form.get("username")
        specialization = request.form.get("specialization")
        experience = request.form.get("experience")

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, hash_password("doctor@123"), "doctor")
            )
            new_user_id = cursor.lastrowid

            dept_row = cursor.execute(
                "SELECT department_id FROM departments WHERE name = ?",
                (specialization,)
            ).fetchone()

            if not dept_row:
                conn.rollback()
                return render_template("adddoctor.html", error="Department not found.", action="Add")

            department_id = dept_row["department_id"]

            cursor.execute("""
                INSERT INTO doctors (username, name, user_id, department, experience, department_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, fullname, new_user_id, specialization, experience, department_id))

            cursor.execute(
                "UPDATE departments SET doctors_registered = doctors_registered + 1 WHERE department_id = ?",
                (department_id,)
            )

            conn.commit()
            return redirect(url_for('admin_doctor'))

        except sqlite3.IntegrityError:
            conn.rollback()
            return render_template("adddoctor.html", error="Username already exists.", action='Add')

        except Exception as e:
            conn.rollback()
            return render_template("adddoctor.html", error=f"Error creating doctor: {e}", action='Add')

        finally:
            conn.close()

    conn = get_db_connection()
    depts = conn.execute("SELECT department_id, name FROM departments").fetchall()
    conn.close()

    return render_template("adddoctor.html", action='Add', departments=[dict(d) for d in depts])


@app.route("/doctor/edit/<int:doctor_id>", methods=["GET", "POST"])
def edit_doctor(doctor_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        fullname = request.form.get("fullname", "").strip()
        username = request.form.get("username", "").strip()
        specialization = request.form.get("specialization", "").strip()
        experience = request.form.get("experience", "").strip()

        redirect_target = None

        try:
            doc_row = cur.execute("SELECT user_id FROM doctors WHERE id = ?", (doctor_id,)).fetchone()

            if not doc_row:
                redirect_target = url_for('admin_doctor')
            else:
                user_id = doc_row["user_id"]

                cur.execute("""
                    UPDATE doctors
                    SET name = ?, department = ?, experience = ?
                    WHERE id = ?
                """, (fullname, specialization, experience, doctor_id))

                if username:
                    cur.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))

                conn.commit()
                flash("Doctor updated successfully.", "success")
                redirect_target = url_for('admin_doctor')

        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Username already exists.", "error")

        except Exception as e:
            conn.rollback()
            flash(f"Error updating doctor: {e}", "error")

        finally:
            conn.close()

        if redirect_target:
            return redirect(redirect_target)

    conn = get_db_connection()
    doctor = conn.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
    depts = conn.execute("SELECT department_id, name FROM departments").fetchall()
    conn.close()

    if not doctor:
        flash("Doctor not found.", "error")
        return redirect(url_for('admin_doctor'))

    return render_template("editdoctor.html", doctor=dict(doctor), departments=[dict(d) for d in depts])


@app.route("/doctor/delete/<int:doctor_id>", methods=["POST"])
def delete_doctor(doctor_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (doctor_id,))
        conn.execute("DELETE FROM doctors WHERE user_id = ?", (doctor_id,))
        conn.commit()
        flash("Doctor deleted successfully.", "success")

    except Exception as e:
        conn.rollback()
        flash(f"Error deleting doctor: {e}", "error")

    finally:
        conn.close()

    return redirect(url_for('admin_doctor'))


@app.route("/doctor/toggle/<int:doctor_id>", methods=["POST"])
def toggle_blacklist_doctor(doctor_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    row = cur.execute("SELECT blacklisted FROM doctors WHERE id = ?", (doctor_id,)).fetchone()

    if not row:
        flash("Doctor not found.", "error")
        return redirect(url_for('admin_doctor'))

    new_status = 0 if row["blacklisted"] == 1 else 1

    cur.execute("UPDATE doctors SET blacklisted = ? WHERE id = ?", (new_status, doctor_id))
    conn.commit()
    conn.close()

    flash("Status updated successfully.", "success")
    return redirect(url_for('admin_doctor'))


@app.route("/adddepartment.html", methods=["GET", "POST"])
def add_department():
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    if request.method == "POST":
        name = request.form.get("fullname")
        description = request.form.get("description")

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO departments(name, description) VALUES (?, ?)", (name, description))
            conn.commit()
            return redirect(url_for('admin_department'))

        except sqlite3.IntegrityError:
            conn.rollback()
            return render_template("adddepartment.html", error="Invalid or duplicate department.", action='Add')

        except Exception as e:
            conn.rollback()
            return render_template("adddepartment.html", error=f"Error creating department: {e}", action='Add')

        finally:
            conn.close()

    return render_template("adddepartment.html", action='Add')


@app.route("/contact.html")
def contact():
    return render_template("contact.html")


@app.route("/about.html")
def about():
    return render_template("about.html")


@app.route("/adminhome.html")
def admin_home():
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    data = fetch_admin_dashboard_data()
    return render_template("adminhome.html", data=data)


@app.route("/admindocotor.html")
def admin_doctor():
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    data = fetch_admin_dashboard_data()
    return render_template("admindoctor.html", data=data)


@app.route("/adminpatient.html")
def admin_patient():
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    data = fetch_admin_dashboard_data()
    return render_template("adminpatient.html", data=data)


@app.route("/patient/edit/<int:patients_id>", methods=["GET", "POST"])
def edit_patients(patients_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()

        if not name or not username:
            flash("Name and username are required.", "error")
            patient = cur.execute("SELECT * FROM patients WHERE id = ?", (patients_id,)).fetchone()
            conn.close()
            return render_template("editpatient.html", patient=dict(patient))

        try:
            row = cur.execute("SELECT user_id FROM patients WHERE id = ?", (patients_id,)).fetchone()
            if not row:
                conn.close()
                flash("Patient not found.", "error")
                return redirect(url_for('admin_patient'))

            user_id = row["user_id"]

            cur.execute("UPDATE patients SET name = ?, username = ? WHERE id = ?",
                        (name, username, patients_id))
            cur.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))

            conn.commit()
            conn.close()
            flash("Patient updated successfully.", "success")
            return redirect(url_for('admin_patient'))

        except sqlite3.IntegrityError:
            conn.rollback()
            conn.close()
            flash("Username already exists.", "error")

            conn = get_db_connection()
            patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patients_id,)).fetchone()
            conn.close()
            return render_template("editpatient.html", patient=dict(patient))

        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f"Error updating patient: {e}", "error")
            return redirect(url_for('admin_patient'))

    patient = cur.execute("SELECT * FROM patients WHERE id = ?", (patients_id,)).fetchone()
    conn.close()

    if not patient:
        flash("Patient not found.", "error")
        return redirect(url_for('admin_patient'))

    return render_template("editpatient.html", patient=dict(patient))


@app.route("/patient/delete/<int:patients_id>", methods=["POST"])
def delete_patients(patients_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (patients_id,))
        conn.execute("DELETE FROM patients WHERE user_id = ?", (patients_id,))
        conn.commit()
        flash("Patient deleted successfully.", "success")

    except Exception as e:
        conn.rollback()
        flash(f"Error deleting patient: {e}", "error")

    finally:
        conn.close()

    return redirect(url_for('admin_patient'))


@app.route("/patient/toggle/<int:patients_id>", methods=["POST"])
def toggle_blacklist_patient(patients_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    row = cur.execute("SELECT blacklisted FROM patients WHERE id = ?", (patients_id,)).fetchone()

    if not row:
        flash("Patient not found.", "error")
        return redirect(url_for('admin_patient'))

    new_status = 0 if row["blacklisted"] == 1 else 1

    cur.execute("UPDATE patients SET blacklisted = ? WHERE id = ?", (new_status, patients_id))
    conn.commit()
    conn.close()

    flash("Patient status updated successfully.", "success")
    return redirect(url_for('admin_patient'))


@app.route("/patient/blacklist/<int:patients_id>", methods=["POST"])
def blacklist_patients(patients_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        conn.execute("ALTER TABLE patients ADD COLUMN blacklisted INTEGER DEFAULT 0")

    except Exception:
        pass

    try:
        conn.execute("UPDATE patients SET blacklisted = 1 WHERE id = ?", (patients_id,))
        conn.commit()
        flash("Patient blacklisted successfully.", "success")

    except Exception as e:
        conn.rollback()
        flash(f"Error blacklisting patient: {e}", "error")

    finally:
        conn.close()

    return redirect(url_for('admin_patient'))


@app.route("/adminappintment.html")
def admin_appointment():
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    data = fetch_admin_dashboard_data()
    return render_template("adminappointment.html", data=data)


@app.route("/admindepartment.html")
def admin_department():
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    data = fetch_admin_dashboard_data()
    return render_template("admindepartment.html", data=data)


@app.route("/admindepartmentview/<int:department_id>", methods=["GET"])
def admindepartmentview(department_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        department = conn.execute(
            "SELECT department_id, name, description, doctors_registered FROM departments WHERE department_id = ?",
            (department_id,)
        ).fetchone()

        doctors = []
        if department:
            doctors = conn.execute(
                "SELECT id, name, experience, blacklisted, department_id FROM doctors WHERE department_id = ?",
                (department_id,)
            ).fetchall()

        admin_data = fetch_admin_dashboard_data()

    finally:
        conn.close()

    if not department:
        flash("Department not found.", "error")
        return redirect(url_for('admin_department'))

    return render_template(
        "admindepartmentview.html",
        data=admin_data,
        department=dict(department),
        doctors=[dict(d) for d in doctors]
    )


@app.route("/doctorhome.html")
def doctor_home():
    doctor_name = session.get('doctor_name')

    if session.get('user_role') != 'doctor' or not doctor_name:
        return redirect(url_for('login'))

    if 'user_id' not in session:
        return redirect(url_for('login'))

    data = fetch_doctor_dashboard_data(doctor_name)
    return render_template("doctorhome.html", doctor={'name': doctor_name}, data=data)


@app.route("/doctor/availability", methods=["GET", "POST"])
def doctor_availability():
    if session.get("user_role") != "doctor":
        return redirect(url_for("login"))

    doctor_name = session.get("doctor_name")
    conn = get_db_connection()
    doc_row = conn.execute("SELECT id, name FROM doctors WHERE name = ?", (doctor_name,)).fetchone()

    if not doc_row:
        conn.close()
        flash("Doctor record not found.", "error")
        return redirect(url_for("doctor_home"))

    doctor_id = doc_row["id"]

    if request.method == "POST":
        try:
            payload = request.get_json(force=True)
            avail_map = payload.get("availability")

        except Exception:
            payload = None
            avail_map = None

        if payload and avail_map is not None:
            cur = conn.cursor()

            for dstr, slots in avail_map.items():
                for slot_key, val in slots.items():
                    try:
                        val_int = 1 if int(val) else 0
                    except Exception:
                        val_int = 1 if bool(val) else 0

                    cur.execute("""
                        INSERT INTO doctor_availability (doctor_id, date, slot, status)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(doctor_id, date, slot)
                        DO UPDATE SET status = excluded.status
                    """, (doctor_id, dstr, slot_key, val_int))

            conn.commit()
            conn.close()

            if request.is_json:
                return {"status": "ok", "message": "Availability updated."}, 200

            flash("Availability updated.", "success")
            return redirect(url_for("doctor_availability"))

        if request.form:
            form_avail = {}
            for key in request.form:
                if key.startswith("avail-"):
                    try:
                        _, dstr, slot_key = key.split("-", 2)
                        form_avail.setdefault(dstr, {})[slot_key] = 1
                    except ValueError:
                        pass

            cur = conn.cursor()
            today = date.today()
            num_days = 7

            for n in range(num_days):
                d = today + timedelta(days=n)
                dstr = d.isoformat()

                for slot_key, _ in SLOTS:
                    val = form_avail.get(dstr, {}).get(slot_key, 0)
                    cur.execute("""
                        INSERT INTO doctor_availability (doctor_id, date, slot, status)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(doctor_id, date, slot)
                        DO UPDATE SET status = excluded.status
                    """, (doctor_id, dstr, slot_key, val))

            conn.commit()
            conn.close()
            flash("Availability updated.", "success")
            return redirect(url_for("doctor_availability"))

    days = []
    today = date.today()
    num_days = 7
    dates = [(today + timedelta(days=i)) for i in range(num_days)]
    date_strs = [d.isoformat() for d in dates]

    q = "SELECT date, slot, status FROM doctor_availability WHERE doctor_id = ? AND date BETWEEN ? AND ?"
    rows = conn.execute(q, (doctor_id, date_strs[0], date_strs[-1])).fetchall()
    conn.close()

    avail = {}
    for r in rows:
        avail.setdefault(r["date"], {})[r["slot"]] = r["status"]

    for d in dates:
        dstr = d.isoformat()
        weekday = d.strftime("%a")
        slots = []

        for slot_key, slot_label in SLOTS:
            status = avail.get(dstr, {}).get(slot_key, 0)
            slots.append({"key": slot_key, "label": slot_label, "status": status})

        days.append({
            "date": dstr,
            "display": f"{d.strftime('%d/%m/%Y')} ({weekday})",
            "slots": slots
        })

    return render_template("doctoravailability.html", doctor=dict(doc_row), days=days)


@app.route("/doctorassigned.html")
def doctor_assigned():
    doctor_name = session.get('doctor_name')

    if session.get('user_role') != 'doctor' or not doctor_name:
        return redirect(url_for('login'))

    data = fetch_doctor_dashboard_data(doctor_name)
    return render_template("doctorassigned.html", doctor={'name': doctor_name}, data=data)


SLOTS = [("morning", "08:00 - 12:00"), ("afternoon", "12:00 - 16:00")]


def ensure_doctor_row_by_name(conn, doctor_name):
    row = conn.execute("SELECT id FROM doctors WHERE name = ?", (doctor_name,)).fetchone()
    return row["id"] if row else None


@app.route("/doctorview/<int:doctor_id>")
def patient_doctor_view(doctor_id):
    if session.get('user_role') != 'patient':
        return redirect(url_for('login'))

    conn = get_db_connection()
    doctor = conn.execute(
        "SELECT id, name, department, experience, blacklisted FROM doctors WHERE id = ?",
        (doctor_id,)
    ).fetchone()

    if not doctor:
        conn.close()
        flash("Doctor not found.", "error")
        return redirect(url_for('patient_department'))

    days = []
    today = date.today()
    num_days = 7
    dates = [(today + timedelta(days=i)) for i in range(num_days)]
    date_strs = [d.isoformat() for d in dates]

    q = "SELECT date, slot, status FROM doctor_availability WHERE doctor_id = ? AND date BETWEEN ? AND ?"
    rows = conn.execute(q, (doctor_id, date_strs[0], date_strs[-1])).fetchall()

    avail = {}
    for r in rows:
        avail.setdefault(r["date"], {})[r["slot"]] = r["status"]

    try:
        SLOTS
    except Exception:
        local_slots = [("morning", "08:00 - 12:00"), ("evening", "16:00 - 20:00")]
    else:
        local_slots = SLOTS

    for d in dates:
        dstr = d.isoformat()
        weekday = d.strftime("%a")
        slots = []

        for slot_key, slot_label in local_slots:
            status = avail.get(dstr, {}).get(slot_key, 0)
            slots.append({"key": slot_key, "label": slot_label, "status": int(status)})

        days.append({
            "date": dstr,
            "display": f"{d.strftime('%d/%m/%Y')} ({weekday})",
            "slots": slots
        })

    has_availability = any(
        int(s.get('status', 0)) == 1
        for day in days
        for s in day['slots']
    )

    conn.close()

    return render_template(
        "patientdoctorview.html",
        doctor=dict(doctor),
        days=days,
        has_availability=has_availability
    )


@app.route("/doctor/check_availability/<int:doctor_id>")
def doctoravailability(doctor_id):
    if session.get('user_role') != 'patient':
        return redirect(url_for('login'))

    conn = get_db_connection()
    doctor = conn.execute("""
        SELECT id, name, department, experience, blacklisted, department_id
        FROM doctors
        WHERE id = ?
    """, (doctor_id,)).fetchone()

    if not doctor:
        conn.close()
        flash("Doctor not found.", "error")
        return redirect(url_for("patient_department"))

    count = conn.execute("""
        SELECT COUNT(*) AS total
        FROM appointments
        WHERE doctor_name = ?
    """, (doctor["name"],)).fetchone()

    conn.close()

    booked_count = count["total"]
    max_slots = 10
    available_slots = max(max_slots - booked_count, 0)

    return render_template(
        "patientdoctoravailability.html",
        doctor=dict(doctor),
        booked_count=booked_count,
        available_slots=available_slots,
        max_slots=max_slots
    )


SLOT_DEFS = [
    {"key": "morning", "label": "08:00 - 12:00"},
    {"key": "evening", "label": "16:00 - 20:00"},
]


def next_7_days():
    today = date.today()
    return [{
        "date": (today + timedelta(days=i)).isoformat(),
        "display": (today + timedelta(days=i)).strftime("%a, %b %d")
    } for i in range(7)]


@app.route('/patient/doctor/<int:doctor_id>/availability')
def patientdoctoravailability(doctor_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, name, department, experience, blacklisted FROM doctors WHERE id = ?", (doctor_id,))
    doctor = cur.fetchone()

    if doctor is None:
        conn.close()
        return "Doctor not found", 404

    today = date.today()
    days_raw = [{
        "date": (today + timedelta(days=i)).isoformat(),
        "display": (today + timedelta(days=i)).strftime("%a, %b %d")
    } for i in range(7)]

    date_list = [d["date"] for d in days_raw]
    placeholders = ",".join("?" for _ in date_list)

    cur.execute(f"""
        SELECT date, slot, status
        FROM doctor_availability
        WHERE doctor_id = ?
          AND date IN ({placeholders})
    """, (doctor_id, *date_list))

    avail_rows = cur.fetchall()
    avail_map = {}

    for r in avail_rows:
        avail_map.setdefault(r["date"], {})[r["slot"]] = r["status"]

    cur.execute(f"""
        SELECT date, slot, COUNT(*) AS cnt
        FROM appointments
        WHERE doctor_id = ?
          AND date IN ({placeholders})
          AND status = 'confirmed'
        GROUP BY date, slot
    """, (doctor_id, *date_list))

    booked_rows = cur.fetchall()
    booked_map = {}

    for r in booked_rows:
        booked_map.setdefault(r["date"], {})[r["slot"]] = r["cnt"]

    days = []

    for d in days_raw:
        slots = []

        for s in SLOT_DEFS:
            status = avail_map.get(d["date"], {}).get(s["key"], 0)
            already_booked = booked_map.get(d["date"], {}).get(s["key"], 0) > 0

            slots.append({
                "key": s["key"],
                "label": s["label"],
                "status": 1 if int(status) == 1 else 0,
                "booked": already_booked
            })

        days.append({
            "date": d["date"],
            "display": d["display"],
            "slots": slots
        })

    conn.close()

    return render_template(
        "patientdoctoravailability.html",
        doctor=doctor,
        doctor_id=doctor_id,
        days=days
    )


@app.route('/patient/book', methods=['POST'])
def patient_book_slot():
    if "user_id" not in session:
        return jsonify({"ok": False, "message": "Login required"}), 401

    data = request.get_json() or {}
    doctor_id = data.get("doctor_id")
    slot_date = data.get("date")
    slot = data.get("slot")
    patient_id = session["user_id"]
    patient_name = session.get("username") or session.get("patient_name") or ""

    if not (doctor_id and slot_date and slot):
        return jsonify({"ok": False, "message": "Missing parameters"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, name, department, blacklisted FROM doctors WHERE id = ?", (doctor_id,))
    doc = cur.fetchone()

    if doc is None:
        conn.close()
        return jsonify({"ok": False, "message": "Doctor not found"}), 404

    if doc["blacklisted"] == 0:
        conn.close()
        return jsonify({"ok": False, "message": "Doctor is blocked"}), 403

    cur.execute("""
        SELECT status FROM doctor_availability
        WHERE doctor_id = ? AND date = ? AND slot = ?
    """, (doctor_id, slot_date, slot))

    avail = cur.fetchone()

    if not avail or int(avail["status"]) != 1:
        conn.close()
        return jsonify({"ok": False, "message": "Slot unavailable"}), 409

    try:
        conn.execute("BEGIN")

        cur.execute("""
            SELECT COUNT(*) AS cnt FROM appointments
            WHERE doctor_id = ? AND date = ? AND slot = ? AND status = 'confirmed'
        """, (doctor_id, slot_date, slot))

        cnt = cur.fetchone()["cnt"]

        if cnt > 0:
            conn.execute("ROLLBACK")
            conn.close()
            return jsonify({"ok": False, "message": "Slot already booked"}), 409

        now = datetime.utcnow().isoformat()

        cur.execute("""
            INSERT INTO appointments
                (patient_name, patient_id, doctor_name, doctor_id, date, slot, department, sr_no, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            patient_name,
            patient_id,
            doc["name"],
            doctor_id,
            slot_date,
            slot,
            doc["department"],
            None,
            now,
            'confirmed'
        ))

        conn.commit()
        appointment_id = cur.lastrowid

    except sqlite3.Error:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass

        conn.close()
        return jsonify({"ok": False, "message": "Booking failed"}), 500

    conn.close()

    return jsonify({"ok": True, "message": "Appointment confirmed", "appointment_id": appointment_id})


@app.route("/patienthome.html")
def patienthome():
    patient_name = session.get('patient_name')

    if session.get('user_role') != 'patient' or not patient_name:
        return redirect(url_for('login'))

    data = fetch_patient_dashboard_data(patient_name)
    return render_template("patienthome.html", data=data)


@app.route("/patientdepartmentview/<int:department_id>", methods=["GET"])
def patientdepartmentview(department_id):
    patient_name = session.get('patient_name')

    if session.get('user_role') != 'patient' or not patient_name:
        return redirect(url_for('login'))

    conn = get_db_connection()

    department = conn.execute(
        "SELECT department_id, name, description, doctors_registered FROM departments WHERE department_id = ?",
        (department_id,)
    ).fetchone()

    doctors = conn.execute(
        "SELECT id, name, experience, blacklisted FROM doctors WHERE department = ?",
        (department['name'],)
    ).fetchall() if department else []

    patient_data = fetch_patient_dashboard_data(patient_name)
    conn.close()

    return render_template(
        "patientdepartmentview.html",
        data=patient_data,
        department=dict(department) if department else None,
        doctors=[dict(d) for d in doctors]
    )


@app.route("/departments.html")
def departments():
    if not session.get('user_id'):
        return redirect(url_for('login'))
    return render_template("departmentdetails.html")


@app.route("/patientdepartment.html")
def patient_department():
    patient_name = session.get('patient_name')

    if session.get('user_role') != 'patient' or not patient_name:
        return redirect(url_for('login'))

    data = fetch_patient_dashboard_data(patient_name)
    return render_template("patientdepartment.html", data=data)


@app.route("/patienthistory.html")
def patient_history():
    if session.get('user_role') not in ['patient']:
        return redirect(url_for('login'))

    default_patient = session.get('patient_name')
    data = request.args.get('patient_name', default_patient)
    data = fetch_patient_history(data)

    return render_template("patienthistory.html", data=data)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.route("/_list_endpoints")
def _list_endpoints():
    return "<br>".join(sorted(rule.endpoint for rule in app.url_map.iter_rules()))

# ===================== RUN =====================
if __name__ == "__main__":
    init_db()
    app.run(debug=True)
