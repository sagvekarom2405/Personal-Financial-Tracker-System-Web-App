from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, make_response
from flask import g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta
import MySQLdb
from MySQLdb.cursors import DictCursor
import csv
import io
from fpdf import FPDF
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import re
app = Flask(__name__)
app.secret_key = "super_secret_key"  # Needed for session
CORS(app)
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'om@24'  # replace with your password
app.config['MYSQL_DB'] = 'finance_tracker'
app.config['MYSQL_CURSORCLASS'] = 'DictCursor' # Makes query results easier to work with

class FlaskMySQLCompat:
    def __init__(self, app):
        self.app = app
        app.teardown_appcontext(self._teardown)

    @property
    def connection(self):
        if 'mysql_connection' not in g:
            cursor_class = DictCursor if self.app.config.get('MYSQL_CURSORCLASS') == 'DictCursor' else None
            connection_kwargs = {
                'host': self.app.config.get('MYSQL_HOST', 'localhost'),
                'user': self.app.config.get('MYSQL_USER'),
                'passwd': self.app.config.get('MYSQL_PASSWORD'),
                'db': self.app.config.get('MYSQL_DB'),
                'cursorclass': cursor_class,
            }
            g.mysql_connection = MySQLdb.connect(
                **{key: value for key, value in connection_kwargs.items() if value is not None}
            )
        return g.mysql_connection

    def _teardown(self, exception):
        connection = g.pop('mysql_connection', None)
        if connection is not None:
            connection.close()


mysql = FlaskMySQLCompat(app)

INCOME_CATEGORY_KEYWORDS = {
    "salary": ["salary", "payroll", "paycheck", "wage", "stipend"],
    "freelance": ["freelance", "client", "project", "gig", "contract"],
    "investment": ["dividend", "interest", "stock", "mutual fund", "sip", "investment", "return"],
    "bonus": ["bonus", "incentive", "reward"],
    "other_income": ["refund", "cashback", "gift", "rebate", "other"]
}

EXPENSE_CATEGORY_KEYWORDS = {
    "food": ["food", "dinner", "lunch", "breakfast", "restaurant", "cafe", "swiggy", "zomato", "grocery"],
    "transport": ["uber", "ola", "fuel", "petrol", "diesel", "metro", "bus", "train", "transport", "taxi"],
    "shopping": ["shopping", "amazon", "flipkart", "mall", "clothes", "purchase"],
    "bills": ["bill", "electricity", "water", "rent", "internet", "wifi", "utility", "recharge", "emi"],
    "entertainment": ["movie", "netflix", "spotify", "game", "concert", "entertainment", "subscription"],
    "healthcare": ["doctor", "hospital", "clinic", "medicine", "medical", "health", "pharmacy"],
    "education": ["course", "book", "tuition", "school", "college", "education", "exam"],
    "other_expense": ["other", "misc", "miscellaneous"]
}


def normalize_text(value):
    return re.sub(r"[^a-z0-9\s]", " ", (value or "").lower()).strip()


def parse_relative_transaction_date(text):
    normalized = normalize_text(text)
    today = date.today()

    if "day before yesterday" in normalized:
        return today - timedelta(days=2), "Detected 'day before yesterday'."
    if "yesterday" in normalized:
        return today - timedelta(days=1), "Detected 'yesterday'."
    if "today" in normalized or "now" in normalized:
        return today, "Detected 'today'."
    if "tomorrow" in normalized:
        return today + timedelta(days=1), "Detected 'tomorrow'."

    explicit_date_patterns = [
        (r"\b(\d{4}-\d{2}-\d{2})\b", "%Y-%m-%d"),
        (r"\b(\d{2}/\d{2}/\d{4})\b", "%d/%m/%Y"),
        (r"\b(\d{2}-\d{2}-\d{4})\b", "%d-%m-%Y"),
    ]

    for pattern, fmt in explicit_date_patterns:
        match = re.search(pattern, text or "")
        if match:
            try:
                return datetime.strptime(match.group(1), fmt).date(), f"Detected explicit date {match.group(1)}."
            except ValueError:
                continue

    return today, "No date found, defaulted to today."


def infer_transaction_type_from_text(text):
    normalized = normalize_text(text)

    income_keywords = [
        "salary", "earned", "received", "income", "bonus", "refund", "cashback",
        "credited", "freelance", "dividend", "interest", "sold"
    ]
    expense_keywords = [
        "spent", "paid", "bought", "purchase", "purchased", "expense", "debited",
        "recharged", "ordered", "rent", "bill", "uber", "zomato", "swiggy"
    ]

    if any(keyword in normalized for keyword in income_keywords):
        return "income", "Detected income-related wording."
    if any(keyword in normalized for keyword in expense_keywords):
        return "expense", "Detected expense-related wording."
    return "expense", "No clear type found, defaulted to expense."


def extract_amount_from_text(text):
    amount_match = re.search(r"(?<!\d)(?:rs\.?|inr|₹|\$)?\s*(\d+(?:\.\d{1,2})?)(?!\d)", text or "", re.IGNORECASE)
    if not amount_match:
        return None, "No amount detected."
    return float(amount_match.group(1)), f"Detected amount {amount_match.group(1)}."


def build_description_from_text(text):
    description = (text or "").strip()
    cleanup_patterns = [
        r"\b(?:today|yesterday|tomorrow|day before yesterday|now)\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{2}/\d{2}/\d{4}\b",
        r"\b\d{2}-\d{2}-\d{4}\b",
        r"(?<!\d)(?:rs\.?|inr|₹|\$)?\s*\d+(?:\.\d{1,2})?(?!\d)",
    ]

    for pattern in cleanup_patterns:
        description = re.sub(pattern, " ", description, flags=re.IGNORECASE)

    description = re.sub(r"\b(?:on|for|at|via|from)\b", " ", description, flags=re.IGNORECASE)
    description = re.sub(r"\s+", " ", description).strip(" ,.-")
    return description or (text or "").strip()


def parse_transaction_command(text):
    text = (text or "").strip()
    if not text:
        return {
            "success": False,
            "message": "Enter a sentence like 'Spent 450 on Uber today' or 'Received salary 55000'."
        }

    transaction_type, type_reason = infer_transaction_type_from_text(text)
    amount, amount_reason = extract_amount_from_text(text)
    parsed_date, date_reason = parse_relative_transaction_date(text)
    description = build_description_from_text(text)

    if amount is None:
        return {
            "success": False,
            "message": "I could not find the amount. Try something like 'Spent 450 on groceries today'."
        }

    category_suggestion = suggest_transaction_category(transaction_type, description, amount)

    return {
        "success": True,
        "type": transaction_type,
        "amount": amount,
        "date": parsed_date.isoformat(),
        "description": description,
        "category": category_suggestion["category"],
        "confidence": category_suggestion["confidence"],
        "reason": " ".join([
            type_reason,
            amount_reason,
            date_reason,
            category_suggestion["reason"]
        ])
    }


def suggest_transaction_category(transaction_type, description, amount=None):
    description_text = normalize_text(description)
    category_map = INCOME_CATEGORY_KEYWORDS if transaction_type == "income" else EXPENSE_CATEGORY_KEYWORDS

    if description_text:
        for category, keywords in category_map.items():
            if any(keyword in description_text for keyword in keywords):
                return {
                    "category": category,
                    "confidence": "high",
                    "reason": f"Matched keywords in the description for {category.replace('_', ' ')}."
                }

    try:
        numeric_amount = float(amount or 0)
    except (TypeError, ValueError):
        numeric_amount = 0

    if transaction_type == "income":
        if numeric_amount >= 50000:
            category = "salary"
        elif numeric_amount >= 5000:
            category = "freelance"
        else:
            category = "other_income"
    else:
        if numeric_amount >= 10000:
            category = "bills"
        elif numeric_amount >= 3000:
            category = "shopping"
        else:
            category = "other_expense"

    return {
        "category": category,
        "confidence": "medium",
        "reason": "Used amount and transaction type because the description was too short or generic."
    }


def build_dashboard_ai_insights(transactions, goals, total_income, total_expense, balance, currency):
    insights = {
        "overview": "Add a few more transactions to unlock sharper spending guidance.",
        "highlights": [],
        "actions": [],
        "health_score": 50
    }

    if not transactions:
        insights["actions"].append("Start by logging your recurring income and your top three monthly expenses.")
        insights["actions"].append("Use clear descriptions like 'Uber to office' or 'Salary for March' for better suggestions.")
        return insights

    expense_rows = [t for t in transactions if t.get("type") == "expense"]
    income_rows = [t for t in transactions if t.get("type") == "income"]

    expense_by_category = {}
    for row in expense_rows:
        category = row.get("category") or "other_expense"
        expense_by_category[category] = expense_by_category.get(category, 0) + float(row.get("amount") or 0)

    if expense_by_category:
        top_category = max(expense_by_category, key=expense_by_category.get)
        top_amount = expense_by_category[top_category]
        insights["highlights"].append(
            f"Your biggest spending category is {top_category.replace('_', ' ')}, totaling {currency}{top_amount:,.2f}."
        )

    if total_income > 0:
        spend_ratio = (total_expense / total_income) * 100
        savings_ratio = (balance / total_income) * 100

        if savings_ratio >= 30:
            insights["overview"] = "Your cash flow looks strong right now, with healthy room left after expenses."
        elif savings_ratio >= 10:
            insights["overview"] = "You are staying positive, but there is room to tighten spending and improve savings."
        else:
            insights["overview"] = "Expenses are eating into most of your income, so this is a good time to rebalance."

        insights["highlights"].append(
            f"You are spending {spend_ratio:.1f}% of income and keeping {max(savings_ratio, 0):.1f}% as net savings."
        )

        if spend_ratio > 85:
            insights["actions"].append("Try capping the highest expense category next month to protect your savings buffer.")
        elif spend_ratio < 60:
            insights["actions"].append("You have room to move some surplus into a goal or emergency fund.")
    else:
        insights["overview"] = "No income entries are recorded yet, so expense guidance is based only on outflows."
        insights["actions"].append("Log your salary or other income to make your dashboard insights more accurate.")

    if len(expense_rows) >= 3:
        average_expense = sum(float(t.get("amount") or 0) for t in expense_rows) / len(expense_rows)
        unusually_large = [
            t for t in expense_rows
            if float(t.get("amount") or 0) >= average_expense * 1.75
        ]
        if unusually_large:
            largest = max(unusually_large, key=lambda row: float(row.get("amount") or 0))
            insights["highlights"].append(
                f"One expense stands out: {largest.get('category', 'expense')} at {currency}{float(largest.get('amount') or 0):,.2f}."
            )

    if goals:
        upcoming_goal = None
        for goal in goals:
            target_date = goal.get("target_date")
            if target_date and target_date >= date.today():
                upcoming_goal = goal
                break

        progressing_goals = []
        for goal in goals:
            target_amount = float(goal.get("target_amount") or 0)
            current_amount = float(goal.get("current_amount") or 0)
            if target_amount > 0:
                progressing_goals.append((current_amount / target_amount) * 100)

        if progressing_goals:
            avg_progress = sum(progressing_goals) / len(progressing_goals)
            insights["highlights"].append(f"Your average goal progress is {avg_progress:.1f}% across {len(progressing_goals)} goals.")

        if upcoming_goal:
            remaining = max(float(upcoming_goal.get("target_amount") or 0) - float(upcoming_goal.get("current_amount") or 0), 0)
            insights["actions"].append(
                f"Your next deadline is '{upcoming_goal.get('title')}'. You still need {currency}{remaining:,.2f} to finish it."
            )

    score = 50
    if total_income > 0:
        score += 20 if balance > 0 else -15
        score += 15 if (balance / total_income) >= 0.2 else 0
        score -= 10 if (total_expense / total_income) > 0.9 else 0
    if goals:
        score += 10
    if expense_rows and len(expense_rows) >= 5:
        score += 5
    insights["health_score"] = max(0, min(100, int(score)))

    if not insights["actions"]:
        insights["actions"].append("Keep descriptions detailed when adding transactions so future insights stay useful.")

    return insights

def ensure_tables():
    """Helper to ensure all necessary tables exist."""
    try:
        cur = mysql.connection.cursor()
        # Users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email VARCHAR(255) PRIMARY KEY,
                password_hash VARCHAR(255) NOT NULL,
                full_name VARCHAR(255),
                phone VARCHAR(20),
                website VARCHAR(255),
                role VARCHAR(20) DEFAULT 'user',
                is_paid BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Transactions table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_email VARCHAR(255),
                type VARCHAR(20),
                amount DECIMAL(10, 2),
                category VARCHAR(50),
                date DATE,
                description TEXT,
                flagged BOOLEAN DEFAULT FALSE
            )
        """)
        # Goals table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_email VARCHAR(255),
                title VARCHAR(100),
                category VARCHAR(50),
                target_amount DECIMAL(10, 2),
                current_amount DECIMAL(10, 2) DEFAULT 0,
                target_date DATE,
                approved BOOLEAN DEFAULT FALSE
            )
        """)
        ensure_payment_table() # Reuse existing helper
        mysql.connection.commit()
        cur.close()
    except Exception as e:
        print(f"Error ensuring tables: {e}")

def ensure_is_paid_column():
    """Helper to ensure the database has the required column."""
    try:
        cur = mysql.connection.cursor()
        cur.execute("SHOW COLUMNS FROM users LIKE 'is_paid'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE users ADD COLUMN is_paid BOOLEAN DEFAULT FALSE")
            # Check if role column exists before trying to use it
            cur.execute("SHOW COLUMNS FROM users LIKE 'role'")
            if cur.fetchone():
                cur.execute("UPDATE users SET is_paid = TRUE WHERE role = 'admin'")
            mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_full_name_column():
    """Helper to ensure the users table has a full_name column."""
    try:
        cur = mysql.connection.cursor()
        cur.execute("SHOW COLUMNS FROM users LIKE 'full_name'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE users ADD COLUMN full_name VARCHAR(255)")
            mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_website_column():
    """Helper to ensure the users table has a website column."""
    try:
        cur = mysql.connection.cursor()
        cur.execute("SHOW COLUMNS FROM users LIKE 'website'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE users ADD COLUMN website VARCHAR(255)")
            mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_phone_column():
    """Helper to ensure the users table has a phone column."""
    try:
        cur = mysql.connection.cursor()
        cur.execute("SHOW COLUMNS FROM users LIKE 'phone'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE users ADD COLUMN phone VARCHAR(20)")
            mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_payment_table():
    """Helper to ensure the payments table exists."""
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_email VARCHAR(255) NOT NULL,
                payer_name VARCHAR(255),
                payment_method VARCHAR(50),
                payment_details VARCHAR(255),
                amount DECIMAL(10, 2) DEFAULT 499.00,
                date DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_is_active_column():
    try:
        cur = mysql.connection.cursor()
        cur.execute("SHOW COLUMNS FROM users LIKE 'is_active'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
            mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_transaction_flags_columns():
    try:
        cur = mysql.connection.cursor()
        cur.execute("SHOW COLUMNS FROM transactions LIKE 'flagged'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE transactions ADD COLUMN flagged BOOLEAN DEFAULT FALSE")
        # Removed legacy refunded-column support; feature intentionally cleaned up.
        mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_goal_approval_column():
    try:
        cur = mysql.connection.cursor()
        cur.execute("SHOW COLUMNS FROM goals LIKE 'approved'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE goals ADD COLUMN approved BOOLEAN DEFAULT FALSE")
            mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_notification_columns():
    """Helper to ensure the users table has notification preference columns."""
    try:
        cur = mysql.connection.cursor()
        columns = [
            ('notify_general', 'BOOLEAN DEFAULT TRUE'),
            ('notify_expense_limit', 'BOOLEAN DEFAULT TRUE'),
            ('notify_monthly_summary', 'BOOLEAN DEFAULT TRUE'),
            ('notify_daily_reminder', 'BOOLEAN DEFAULT TRUE')
        ]
        for col_name, col_def in columns:
            cur.execute(f"SHOW COLUMNS FROM users LIKE '{col_name}'")
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
        mysql.connection.commit()
        cur.close()
    except Exception:
        pass

def ensure_appearance_columns():
    """Helper to ensure the users table has appearance preference columns."""
    try:
        cur = mysql.connection.cursor()
        columns = [
            ('theme_mode', "VARCHAR(20) DEFAULT 'light'"),
            ('theme_color', "VARCHAR(20) DEFAULT '#4f46e5'"),
            ('currency', "VARCHAR(10) DEFAULT '\u20b9'"),
            ('date_format', "VARCHAR(20) DEFAULT '%Y-%m-%d'")
        ]
        for col_name, col_def in columns:
            cur.execute(f"SHOW COLUMNS FROM users LIKE '{col_name}'")
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
        mysql.connection.commit()
        cur.close()
    except Exception:
        pass


# Legacy duplicated email/helper definitions removed during security cleanup.

def ensure_appearance_columns():
    """Helper to ensure the users table has appearance preference columns."""
    try:
        cur = mysql.connection.cursor()
        columns = [
            ("theme_mode", "VARCHAR(20) DEFAULT 'light'"),
            ("theme_color", "VARCHAR(20) DEFAULT '#4f46e5'"),
            ("currency", "VARCHAR(10) DEFAULT '\u20b9'"),
            ("date_format", "VARCHAR(20) DEFAULT '%Y-%m-%d'")
        ]
        for col_name, col_def in columns:
            cur.execute(f"SHOW COLUMNS FROM users LIKE '{col_name}'")
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
        mysql.connection.commit()
        cur.close()
    except Exception:
        pass


def send_welcome_email(to_email, user_name):
    """Sends a welcome email to the newly registered user."""
    # --- GMAIL CONFIGURATION ---
    # You must generate an App Password for your Gmail account.
    # Go to Google Account > Security > 2-Step Verification > App passwords.
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SENDER_EMAIL = "sagvekarom2405@gmail.com"
    SENDER_PASSWORD = "nhuzqngsdbsltvyw"

    subject = "Welcome to Finance Tracker!"
    body = f"""Hi {user_name},

Welcome to Finance Tracker!

We are excited to have you on board. You can now start tracking your income, expenses, and set financial goals.

Best regards,
The Finance Tracker Team
"""

    msg = MIMEMultipart()
    msg['From'] = f"Finance Tracker <{SENDER_EMAIL}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"Welcome email sent to {to_email}")
    except Exception as e:
        print(f"Failed to send welcome email: {e}")


def send_payment_confirmation_email(to_email, payer_name, amount, method, date_str):
    """Sends a payment confirmation email to the user."""
    # --- GMAIL CONFIGURATION ---
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SENDER_EMAIL = "sagvekarom2405@gmail.com"
    SENDER_PASSWORD = "nhuzqngsdbsltvyw"

    subject = "Payment Receipt - Finance Tracker Premium"
    body = f"""Hi {payer_name or 'User'},

Thank you for upgrading to Finance Tracker Premium!

Here are your transaction details:
--------------------------------------------------
Amount: {amount}
Payment Method: {method}
Date: {date_str}
Status: Successful
--------------------------------------------------

You now have access to advanced analytics, financial goals, and more.

Best regards,
The Finance Tracker Team
"""
    msg = MIMEMultipart()
    msg['From'] = f"Finance Tracker <{SENDER_EMAIL}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"Payment email sent to {to_email}")
    except Exception as e:
        print(f"Failed to send payment email: {e}")


def send_password_reset_notification_email(to_email, user_name, new_password):
    """Sends a notification email after an admin resets a user's password."""
    # --- GMAIL CONFIGURATION ---
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SENDER_EMAIL = "sagvekarom2405@gmail.com"
    SENDER_PASSWORD = "nhuzqngsdbsltvyw"

    subject = "Your Password Has Been Reset"
    body = f"""Hi {user_name},

Your password for Finance Tracker was recently reset.

Your new temporary password is: {new_password}

For your security, please log in and change this password right away.

If you did not request this change or have any concerns, please contact support immediately.

Best regards,
The Finance Tracker Team
"""
    msg = MIMEMultipart()
    msg['From'] = f"Finance Tracker <{SENDER_EMAIL}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"Password reset notification sent to {to_email}")
    except Exception as e:
        print(f"Failed to send password reset notification email: {e}")


@app.route("/")
def home():
    return render_template("login.html")

@app.route("/signup")
def signup_page():
    return render_template("signup.html")

@app.route("/register", methods=["POST"])
def register():
    # Expect JSON data from the frontend
    data = request.get_json()
    if not data:
        return jsonify({"message": "Invalid request. No data provided.", "success": False}), 400

    email = data.get("email")
    password = data.get("password")
    full_name = data.get("full_name")
    website = data.get("website")
    phone = data.get("phone")

    if not email or not password:
        return jsonify({"message": "Email and password are required.", "success": False}), 400

    hashed_password = generate_password_hash(password)
    ensure_full_name_column() # Ensure DB has the column
    ensure_website_column()
    ensure_phone_column()
    cur = mysql.connection.cursor()

    try:
        # Check if user already exists
        cur.execute("SELECT email FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return jsonify({"message": "An account with this email already exists.", "success": False}), 409 # HTTP 409 Conflict

        # Insert new user
        cur.execute("INSERT INTO users (email, password_hash, full_name, website, phone) VALUES (%s, %s, %s, %s, %s)", (email, hashed_password, full_name, website, phone))
        mysql.connection.commit()
        
        # Send Welcome Email
        try:
            send_welcome_email(email, full_name or "User")
        except Exception as e:
            print(f"Email sending error: {e}")
            
        return jsonify({"message": "Account created successfully! Please log in.", "success": True}), 201 # HTTP 201 Created
    except MySQLdb.Error as e:
        mysql.connection.rollback()
        print(f"DATABASE REGISTRATION ERROR: {e}")
        return jsonify({"message": "A database error occurred during registration.", "success": False}), 500
    finally:
        cur.close()

@app.route("/login", methods=["POST"])
def login():
    # Expect JSON data from the frontend
    data = request.get_json()
    if not data:
        return jsonify({"message": "Invalid request. No data provided.", "success": False}), 400

    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"message": "Email and password are required.", "success": False}), 400

    # Ensure DB schema is correct before login
    ensure_tables()
    ensure_is_paid_column() # Run migration if needed
    ensure_notification_columns() # Ensure notification settings exist
    ensure_appearance_columns() # Ensure appearance settings exist

    cur = mysql.connection.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session["user"] = email  # Set user session
            session["role"] = user.get("role", "user") # Set user role
            session["is_paid"] = bool(user.get("is_paid", 0)) # Store payment status
            redirect_url = "/admin" if session["role"] == "admin" else "/dashboard"
            
            # On success, tell the frontend it worked. The frontend will handle the redirect.
            return jsonify({"message": "Login successful!", "success": True, "redirect_url": redirect_url}), 200
        else:
            # Invalid credentials
            return jsonify({"message": "Invalid credentials. Please try again.", "success": False}), 401 # HTTP 401 Unauthorized
    except MySQLdb.Error as e:
        print(f"DATABASE LOGIN ERROR: {e}")
        return jsonify({"message": "A database error occurred during login.", "success": False}), 500
    finally:
        cur.close()

# --- PAYMENT SYSTEM ROUTES ---

@app.route("/payment")
def payment_page():
    if "user" not in session: return redirect(url_for("home"))
    # If already paid or admin, go to dashboard
    if session.get("is_paid") or session.get("role") == "admin":
        return redirect(url_for("dashboard"))
    return render_template("payment.html")

@app.route("/payment/checkout")
def payment_checkout():
    if "user" not in session: return redirect(url_for("home"))
    if session.get("is_paid") or session.get("role") == "admin":
        return redirect(url_for("dashboard"))
    return render_template("payment_checkout.html", user_email=session["user"])

@app.route("/payment/process", methods=["POST"])
def process_payment():
    if "user" not in session: return redirect(url_for("home"))

    # Simulate payment processing (accepting any input as success)
    payer_name = request.form.get('payer_name')
    method = request.form.get('method')
    details = request.form.get('details')
    amount = 499.00
    payment_date = datetime.now()

    # Ensure DB schema is correct before update
    ensure_is_paid_column()
    ensure_payment_table()

    cur = mysql.connection.cursor()
    # Insert payment record
    cur.execute("INSERT INTO payments (user_email, payer_name, payment_method, payment_details, amount, date) VALUES (%s, %s, %s, %s, %s, %s)", (session["user"], payer_name, method, details, amount, payment_date))
    
    # Update user status
    cur.execute("UPDATE users SET is_paid = TRUE WHERE email = %s", (session["user"],))
    mysql.connection.commit()
    cur.close()
    
    session["is_paid"] = True # Update session immediately
    
    # Send Payment Confirmation Email
    try:
        send_payment_confirmation_email(session["user"], payer_name, f"\u20b9{amount:.2f}", method, payment_date.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        print(f"Payment email error: {e}")

    flash("Payment successful! Welcome to Premium.", "success")
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("home"))  # Protect route
        

    user_email = session["user"]
    # Default to email prefix, but try to fetch full name below
    user_name = user_email.split('@')[0] 
    
    try:
        cur = mysql.connection.cursor()

        # Fetch user details (name)
        cur.execute("SELECT full_name, notify_general, notify_expense_limit, notify_monthly_summary, notify_daily_reminder, theme_mode, theme_color, currency, date_format FROM users WHERE email = %s", (user_email,))
        user_row = cur.fetchone()
        notifications = {'general': True, 'expense_limit': True, 'monthly_summary': True, 'daily_reminder': True}
        appearance = {'theme_mode': 'light', 'theme_color': '#4f46e5', 'currency': '\u20b9', 'date_format': '%Y-%m-%d'}
        
        if user_row:
            if user_row.get('full_name'):
                user_name = user_row['full_name']
            notifications = {
                'general': bool(user_row.get('notify_general', True)),
                'expense_limit': bool(user_row.get('notify_expense_limit', True)),
                'monthly_summary': bool(user_row.get('notify_monthly_summary', True)),
                'daily_reminder': bool(user_row.get('notify_daily_reminder', True))
            }
            appearance = {
                'theme_mode': user_row.get('theme_mode') or 'light',
                'theme_color': user_row.get('theme_color') or '#4f46e5',
                'currency': user_row.get('currency') or '\u20b9',
                'date_format': user_row.get('date_format') or '%Y-%m-%d'
            }

        # Fetch summary data for the logged-in user
        cur.execute("SELECT SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) as total_income, SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) as total_expense FROM transactions WHERE user_email = %s", (user_email,))
        summary = cur.fetchone() or {} # Ensure summary is a dict, not None, to prevent errors
        total_income = summary.get('total_income') or 0
        total_expense = summary.get('total_expense') or 0
        balance = total_income - total_expense
        savings_rate = (balance / total_income) * 100 if total_income > 0 else 0

        # Fetch recent transactions for the logged-in user
        cur.execute("SELECT id, type, amount, category, date, description FROM transactions WHERE user_email = %s ORDER BY date DESC, id DESC LIMIT 10", (user_email,))
        transactions = cur.fetchall()

        # Fetch a larger history for AI insights
        cur.execute("SELECT type, amount, category, date, description FROM transactions WHERE user_email = %s ORDER BY date DESC, id DESC LIMIT 120", (user_email,))
        insight_transactions = cur.fetchall()

        # Fetch total transaction count for statistics
        cur.execute("SELECT COUNT(*) as count FROM transactions WHERE user_email = %s", (user_email,))
        transaction_count = cur.fetchone()['count']

        # --- ADD THIS CODE BLOCK ---
        # Fetch financial goals for the logged-in user
        cur.execute("SELECT id, title, category, current_amount, target_amount, target_date FROM goals WHERE user_email = %s ORDER BY target_date ASC", (user_email,))
        goals = cur.fetchall()
        # --- END OF ADDED CODE BLOCK ---
        
        cur.close()
        ai_insights = build_dashboard_ai_insights(
            insight_transactions,
            goals,
            float(total_income or 0),
            float(total_expense or 0),
            float(balance or 0),
            appearance["currency"]
        )
        # --- UPDATE THE LINE BELOW ---
        return render_template(
            "dashboard.html",
            income=total_income,
            expense=total_expense,
            balance=balance,
            transactions=transactions,
            transaction_count=transaction_count,
            savings_rate=savings_rate,
            user_name=user_name,
            goals=goals,
            today_date=date.today(),
            is_admin=session.get("role") == "admin",
            is_paid=session.get("is_paid", False),
            notifications=notifications,
            appearance=appearance,
            ai_insights=ai_insights
        )
    except MySQLdb.Error as e:
        print(f"DATABASE DASHBOARD ERROR: {e}")
        flash("Could not load dashboard data due to a database error.", "error")
        # Render the dashboard with zeroed-out data so the page doesn't crash
        return render_template(
            "dashboard.html",
            income=0,
            expense=0,
            balance=0,
            transactions=[],
            transaction_count=0,
            savings_rate=0,
            user_name=session.get("user", "").split('@')[0],
            goals=[],
            today_date=date.today(),
            is_admin=session.get("role") == "admin",
            is_paid=session.get("is_paid", False),
            notifications={'general': True, 'expense_limit': True, 'monthly_summary': True, 'daily_reminder': True},
            appearance={'theme_mode': 'light', 'theme_color': '#4f46e5', 'currency': '\u20b9', 'date_format': '%Y-%m-%d'},
            ai_insights=build_dashboard_ai_insights([], [], 0, 0, 0, '\u20b9')
        )

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("home"))


# In your main Python file (e.g., app.py)
@app.route('/transaction', methods=['GET', 'POST'])
def handle_transaction():
    if "user" not in session:
        return redirect(url_for("home"))  # Protect route


    user_email = session["user"]

    if request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({"message": "Invalid request. No data provided.", "success": False}), 400

            # --- Data Extraction and Validation ---
            transaction_type = data.get('type')
            # Safely handle amount, converting None or other non-strings to an empty string
            amount_raw = data.get('amount')
            amount_str = str(amount_raw).strip() if amount_raw is not None else ''
            category = data.get('category', '')
            date = data.get('date')
            description = data.get('description', '')

            # Improved validation
            if not all([transaction_type, category, date, amount_str]):
                return jsonify({"message": "Type, Amount, Category, and Date are required fields.", "success": False}), 400

            try:
                amount = float(amount_str)
            except ValueError:
                return jsonify({"message": "Amount must be a valid number.", "success": False}), 400

            if amount <= 0:
                return jsonify({"message": "Amount must be a positive number.", "success": False}), 400

            # --- Database Interaction ---
            cur = mysql.connection.cursor()
            cur.execute(
                "INSERT INTO transactions (user_email, type, amount, category, date, description) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_email, transaction_type, amount, category, date, description)
            )
            mysql.connection.commit()

            cur.close()

            # Since the page redirects, we only need to send a success message.
            # No need to send back summary data that won't be used.
            return jsonify({
                "message": "Transaction added successfully!",
                "success": True
            }), 200
        except ValueError:
            return jsonify({"message": "Amount must be a valid number.", "success": False}), 400
        except MySQLdb.Error as e:
            mysql.connection.rollback()
            print(f"DATABASE TRANSACTION ERROR: {e}")
            return jsonify({
                "message": "A database error occurred. Please check your data is valid and try again.",
                "success": False
            }), 500
        except Exception as e:
            # This is a catch-all for any other unexpected errors
            mysql.connection.rollback() # Good practice to rollback on any error
            print(f"UNEXPECTED ERROR in handle_transaction: {e}")
            return jsonify({
                "message": "An internal server error occurred. Please try again later.",
                "success": False
            }), 500

    # For GET requests
    cur = mysql.connection.cursor()
    cur.execute("SELECT currency FROM users WHERE email = %s", (user_email,))
    user_data = cur.fetchone()
    cur.close()
    currency = user_data['currency'] if user_data and user_data.get('currency') else '\u20b9'
    return render_template('transaction.html', currency=currency)


@app.route('/ai/suggest-transaction', methods=['POST'])
def ai_suggest_transaction():
    if "user" not in session:
        return jsonify({"message": "Unauthorized", "success": False}), 401

    data = request.get_json() or {}
    transaction_type = data.get("type")
    description = data.get("description", "")
    amount = data.get("amount")

    if transaction_type not in {"income", "expense"}:
        return jsonify({"message": "Transaction type is required.", "success": False}), 400

    suggestion = suggest_transaction_category(transaction_type, description, amount)
    return jsonify({
        "success": True,
        "category": suggestion["category"],
        "confidence": suggestion["confidence"],
        "reason": suggestion["reason"]
    })


@app.route('/ai/parse-transaction', methods=['POST'])
def ai_parse_transaction():
    if "user" not in session:
        return jsonify({"message": "Unauthorized", "success": False}), 401

    data = request.get_json() or {}
    sentence = data.get("text", "")
    parsed = parse_transaction_command(sentence)

    status_code = 200 if parsed.get("success") else 400
    return jsonify(parsed), status_code


@app.route('/ai/dashboard-insights')
def ai_dashboard_insights():
    if "user" not in session:
        return jsonify({"message": "Unauthorized", "success": False}), 401

    user_email = session["user"]

    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT currency FROM users WHERE email = %s", (user_email,))
        user_data = cur.fetchone() or {}
        currency = user_data.get('currency') or '\u20b9'

        cur.execute("SELECT type, amount, category, date, description FROM transactions WHERE user_email = %s ORDER BY date DESC, id DESC LIMIT 120", (user_email,))
        transactions = cur.fetchall()

        cur.execute("SELECT id, title, category, current_amount, target_amount, target_date FROM goals WHERE user_email = %s ORDER BY target_date ASC", (user_email,))
        goals = cur.fetchall()
        cur.close()

        total_income = sum(float(t.get("amount") or 0) for t in transactions if t.get("type") == "income")
        total_expense = sum(float(t.get("amount") or 0) for t in transactions if t.get("type") == "expense")
        balance = total_income - total_expense

        return jsonify({
            "success": True,
            "insights": build_dashboard_ai_insights(transactions, goals, total_income, total_expense, balance, currency)
        })
    except MySQLdb.Error as e:
        return jsonify({"message": f"Database error: {e}", "success": False}), 500


@app.route('/add-goal-form')
def add_goal_form():
    if "user" not in session:
        return redirect(url_for("home"))
    
    if session.get("role") != "admin" and not session.get("is_paid", False):
        return redirect(url_for("payment_page"))
    
    user_email = session["user"]
    cur = mysql.connection.cursor()
    cur.execute("SELECT currency FROM users WHERE email = %s", (user_email,))
    user_data = cur.fetchone()
    cur.close()
    currency = user_data['currency'] if user_data and user_data.get('currency') else '\u20b9'
    
    return render_template('add_goal_form.html', currency=currency)

@app.route('/add-goal', methods=['POST'])
def add_goal():
    if "user" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    if session.get("role") != "admin" and not session.get("is_paid", False):
        return jsonify({"success": False, "message": "Payment required to add goals."}), 403


    user_email = session["user"]
    cur = None # Initialize cur to None
    try:
        data = request.get_json()
        if not data:
            return jsonify({"message": "Invalid request. No data provided.", "success": False}), 400

        title = data.get('title')
        category = data.get('category')
        target_amount_raw = data.get('target_amount')
        current_amount_raw = data.get('current_amount', 0) # Default current amount to 0
        target_date_str = data.get('target_date')

        if not all([title, category, target_amount_raw, target_date_str]):
            return jsonify({"message": "Title, Category, Target Amount, and Target Date are required.", "success": False}), 400

        # Validate and convert data
        target_amount = float(target_amount_raw)
        current_amount = float(current_amount_raw)
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()

        if target_amount <= 0:
             return jsonify({"message": "Target Amount must be a positive number.", "success": False}), 400

        cur = mysql.connection.cursor()
        # Add user_email to the INSERT statement
        cur.execute("""INSERT INTO goals (user_email, title, category, target_amount, current_amount, target_date)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (user_email, title, category, target_amount, current_amount, target_date))
        mysql.connection.commit()
        return jsonify({'success': True, 'message': 'Goal added successfully!'}), 201
    except (ValueError, TypeError):
        return jsonify({"message": "Invalid data format for amount or date.", "success": False}), 400
    except MySQLdb.Error as e:
        mysql.connection.rollback()
        # Log the full error to the console for your records
        print(f"DATABASE GOAL ERROR: {e}") 

        # Create a more helpful error message for the frontend
        user_facing_message = "A database error occurred. Please check the console for details."
        if app.debug:
            # When debugging, send the specific DB error string to the browser.
            # Using str(e) is crucial as the raw exception object isn't JSON serializable.
            user_facing_message = f"Database Error: {str(e)}"

        return jsonify({"message": user_facing_message, "success": False}), 500
    except Exception as e:
        # Catch any other unexpected errors to prevent a server crash
        # and ensure a JSON response is always sent for this API endpoint.
        if mysql.connection:
            mysql.connection.rollback()
        print(f"UNEXPECTED GOAL ERROR: {e}")
        return jsonify({"message": "An internal server error occurred.", "success": False}), 500
    finally:
        if cur:
            cur.close()

@app.route('/update_notifications', methods=['POST'])
def update_notifications():
    if "user" not in session:
        return redirect(url_for("home"))
    
    user_email = session["user"]
    
    # Checkboxes return 'on' if checked, nothing if unchecked
    notify_general = request.form.get('notify_general') == 'on'
    notify_expense_limit = request.form.get('notify_expense_limit') == 'on'
    notify_monthly_summary = request.form.get('notify_monthly_summary') == 'on'
    notify_daily_reminder = request.form.get('notify_daily_reminder') == 'on'
    
    try:
        ensure_notification_columns()
        cur = mysql.connection.cursor()
        cur.execute("""
            UPDATE users 
            SET notify_general=%s, notify_expense_limit=%s, notify_monthly_summary=%s, notify_daily_reminder=%s 
            WHERE email=%s
        """, (notify_general, notify_expense_limit, notify_monthly_summary, notify_daily_reminder, user_email))
        mysql.connection.commit()
        cur.close()
        flash("Notification preferences updated!", "success")
    except MySQLdb.Error as e:
        print(f"Error updating notifications: {e}")
        flash("Failed to update preferences.", "error")
        
    return redirect(url_for('dashboard') + "#settings")

@app.route('/update_appearance', methods=['POST'])
def update_appearance():
    if "user" not in session:
        return redirect(url_for("home"))
    
    user_email = session["user"]
    theme_mode = request.form.get('theme_mode', 'light')
    theme_color = request.form.get('theme_color', '#4f46e5')
    currency = request.form.get('currency', 'â‚¹')
    date_format = request.form.get('date_format', '%Y-%m-%d')
    
    try:
        ensure_appearance_columns()
        cur = mysql.connection.cursor()

        # Fetch current currency to handle conversion
        cur.execute("SELECT currency FROM users WHERE email = %s", (user_email,))
        res = cur.fetchone()
        current_currency = res['currency'] if res and res.get('currency') else 'â‚¹'

        if currency != current_currency:
            # Exchange rates relative to INR (Base)
            rates = {'â‚¹': 1.0, '$': 0.012, 'â‚¬': 0.011, 'Â£': 0.0095, 'Â¥': 1.8}
            old_rate = rates.get(current_currency, 1.0)
            new_rate = rates.get(currency, 1.0)
            
            if old_rate > 0:
                factor = new_rate / old_rate
                # Convert Transactions
                cur.execute("UPDATE transactions SET amount = amount * %s WHERE user_email = %s", (factor, user_email))
                # Convert Goals
                cur.execute("UPDATE goals SET target_amount = target_amount * %s, current_amount = current_amount * %s WHERE user_email = %s", (factor, factor, user_email))

        cur.execute("UPDATE users SET theme_mode=%s, theme_color=%s, currency=%s, date_format=%s WHERE email=%s", 
                    (theme_mode, theme_color, currency, date_format, user_email))
        mysql.connection.commit()
        cur.close()
        flash("Appearance settings updated!", "success")
    except MySQLdb.Error as e:
        flash(f"Error updating appearance: {e}", "error")
        
    return redirect(url_for('dashboard') + "#settings")

@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if "user" not in session:
        return redirect(url_for("home"))

    user_email = session["user"]
    cur = mysql.connection.cursor()

    if request.method == 'POST':
        full_name = request.form.get('full_name')
        phone = request.form.get('phone')
        password = request.form.get('password')

        if not full_name or len(full_name.strip()) < 2:
            flash("Full Name is required and must be at least 2 characters.", "error")
            cur.close()
            return redirect(url_for('edit_profile'))

        if phone and (not phone.strip().isdigit() or len(phone.strip()) != 10):
            flash("Phone number must be exactly 10 digits.", "error")
            cur.close()
            return redirect(url_for('edit_profile'))

        try:
            cur.execute("UPDATE users SET full_name = %s, phone = %s WHERE email = %s", (full_name, phone, user_email))
            
            if password:
                hashed_password = generate_password_hash(password)
                cur.execute("UPDATE users SET password_hash = %s WHERE email = %s", (hashed_password, user_email))
            
            mysql.connection.commit()
            flash("Profile updated successfully!", "success")
        except MySQLdb.Error as e:
            print(f"Error updating profile: {e}")
            flash("An error occurred while updating profile.", "error")
        finally:
            cur.close()
        return redirect(url_for('dashboard'))

    cur.execute("SELECT full_name, phone FROM users WHERE email = %s", (user_email,))
    user = cur.fetchone()
    cur.close()

    return render_template('edit_profile.html', user_email=user_email, user=user)

@app.route('/delete_goal/<int:goal_id>', methods=['POST'])
def delete_goal(goal_id):
    if "user" not in session:
        return redirect(url_for("home"))
    
    user_email = session["user"]
    try:
        cur = mysql.connection.cursor()
        cur.execute("DELETE FROM goals WHERE id = %s AND user_email = %s", (goal_id, user_email))
        mysql.connection.commit()
        cur.close()
        flash("Goal deleted successfully!", "success")
    except MySQLdb.Error as e:
        print(f"Error deleting goal: {e}")
        flash("Error deleting goal.", "error")
    
    return redirect(url_for('dashboard'))

@app.route('/edit_goal/<int:goal_id>', methods=['GET', 'POST'])
def edit_goal(goal_id):
    if "user" not in session:
        return redirect(url_for("home"))

    user_email = session["user"]
    cur = mysql.connection.cursor()

    if request.method == 'POST':
        title = request.form.get('title')
        category = request.form.get('category')
        target_amount = request.form.get('target_amount')
        current_amount = request.form.get('current_amount')
        target_date = request.form.get('target_date')

        try:
            target_amount_value = float(target_amount)
            current_amount_value = float(current_amount)
            target_date_value = datetime.strptime(target_date, '%Y-%m-%d').date()

            if not title or not category:
                flash("Title and category are required.", "error")
                return redirect(url_for('edit_goal', goal_id=goal_id))

            if target_amount_value <= 0:
                flash("Target amount must be greater than 0.", "error")
                return redirect(url_for('edit_goal', goal_id=goal_id))

            if current_amount_value < 0:
                flash("Current amount cannot be negative.", "error")
                return redirect(url_for('edit_goal', goal_id=goal_id))

            cur.execute("""
                UPDATE goals 
                SET title=%s, category=%s, target_amount=%s, current_amount=%s, target_date=%s 
                WHERE id=%s AND user_email=%s
            """, (title.strip(), category, target_amount_value, current_amount_value, target_date_value, goal_id, user_email))
            mysql.connection.commit()
            flash("Goal updated successfully!", "success")
            return redirect(url_for('dashboard'))
        except ValueError:
            flash("Please enter valid numeric values and a valid target date.", "error")
        except MySQLdb.Error as e:
            print(f"Error updating goal: {e}")
            flash("Error updating goal.", "error")
    
    # GET request: fetch goal data
    cur.execute("SELECT * FROM goals WHERE id = %s AND user_email = %s", (goal_id, user_email))
    goal = cur.fetchone()

    cur.execute("SELECT currency FROM users WHERE email = %s", (user_email,))
    user_data = cur.fetchone()
    currency = user_data['currency'] if user_data and user_data.get('currency') else '\u20b9'
    cur.close()

    if not goal:
        flash("Goal not found.", "error")
        return redirect(url_for('dashboard'))

    return render_template('edit_goal.html', goal=goal, currency=currency)

@app.route('/expense-category-data')
def expense_category_data():
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    # --- PAYMENT CHECK FOR GRAPHS ---
    if session.get("role") != "admin" and not session.get("is_paid", False):
        return jsonify({"error": "Premium feature", "labels": [], "values": []}), 403
    # --------------------------------

    user_email = session["user"]
    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE user_email = %s AND type = 'expense'
        GROUP BY category
    """, (user_email,))

    results = cur.fetchall()
    cur.close()

    labels = []
    values = []

    for row in results:
        labels.append(row['category'])
        values.append(float(row['total']))

    return jsonify({
        "labels": labels,
        "values": values
    })

@app.route('/income-category-data')
def income_category_data():
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    # --- PAYMENT CHECK FOR GRAPHS ---
    if session.get("role") != "admin" and not session.get("is_paid", False):
        return jsonify({"error": "Premium feature", "labels": [], "values": []}), 403
    # --------------------------------

    user_email = session["user"]
    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE user_email = %s AND type = 'income'
        GROUP BY category
    """, (user_email,))

    results = cur.fetchall()
    cur.close()

    labels = [row['category'] for row in results]
    values = [float(row['total']) for row in results]

    return jsonify({"labels": labels, "values": values})

@app.route("/admin")
def admin_dashboard():
    if "user" not in session or session.get("role") != "admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for("dashboard"))
    # Ensure optional admin columns exist to avoid OperationalError on SELECT
    ensure_is_active_column()
    ensure_transaction_flags_columns()
    ensure_goal_approval_column()

    search_query = request.args.get('search', '')
    cur = mysql.connection.cursor()
    
    # Fetch System Stats
    cur.execute("SELECT COUNT(*) as count FROM users")
    user_count = cur.fetchone()['count']
    
    cur.execute("SELECT COUNT(*) as count FROM transactions")
    transaction_count = cur.fetchone()['count']
    
    # Fetch Total Goals
    cur.execute("SELECT COUNT(*) as count FROM goals")
    goal_count = cur.fetchone()['count']

    # Fetch Total System Balance
    cur.execute("""
        SELECT 
            SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) as total_income, 
            SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) as total_expense 
        FROM transactions
    """)
    balance_result = cur.fetchone()
    system_balance = (balance_result['total_income'] or 0) - (balance_result['total_expense'] or 0) if balance_result else 0

    # Fetch Recent System Activity (include id and flags)
    cur.execute("SELECT id, user_email, type, category, amount, date, IFNULL(flagged, FALSE) as flagged FROM transactions ORDER BY date DESC, id DESC LIMIT 5")
    recent_activity = cur.fetchall()

    # Fetch Recent Payments
    try:
        cur.execute("SELECT * FROM payments ORDER BY date DESC LIMIT 10")
        recent_payments = cur.fetchall()
    except Exception:
        recent_payments = []

    # Fetch Users with Transaction Counts and Search capability
    sql = """
        SELECT u.email, u.role, u.is_active,
        (SELECT COUNT(*) FROM transactions t WHERE t.user_email = u.email) as tx_count 
        FROM users u
    """
    params = []
    if search_query:
        sql += " WHERE u.email LIKE %s"
        params.append(f"%{search_query}%")
    
    cur.execute(sql, tuple(params))
    users = cur.fetchall()

    # Fetch admin currency preference
    cur.execute("SELECT currency FROM users WHERE email = %s", (session["user"],))
    admin_data = cur.fetchone()
    currency = admin_data['currency'] if admin_data and admin_data.get('currency') else '\u20b9'

    cur.close()
    
    return render_template(
        "admin_dashboard.html", 
        user_count=user_count, 
        transaction_count=transaction_count, 
        goal_count=goal_count, 
        system_balance=system_balance, 
        recent_activity=recent_activity,
        recent_payments=recent_payments,
        users=users,
        search_query=search_query,
        currency=currency
    )

@app.route("/admin/delete_user", methods=["POST"])
def delete_user():
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("home"))
        
    email_to_delete = request.form.get('email')
    
    if email_to_delete == session['user']:
        flash("You cannot delete your own admin account.", "error")
        return redirect(url_for("admin_dashboard"))

    cur = mysql.connection.cursor()
    # Delete user and their data (Cascading delete manually if FKs aren't set up to cascade)
    cur.execute("DELETE FROM transactions WHERE user_email = %s", (email_to_delete,))
    cur.execute("DELETE FROM goals WHERE user_email = %s", (email_to_delete,))
    cur.execute("DELETE FROM users WHERE email = %s", (email_to_delete,))
    mysql.connection.commit()
    cur.close()
    flash(f"User {email_to_delete} deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/reset_password", methods=["POST"])
def admin_reset_password():
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("home"))
        
    email = request.form.get('email')
    new_password = request.form.get('new_password')
    
    if not email or not new_password:
        flash("Email and new password are required.", "error")
        return redirect(url_for("admin_dashboard"))

    hashed_password = generate_password_hash(new_password)
    
    try:
        cur = mysql.connection.cursor()

        # Fetch user's full name for the email
        cur.execute("SELECT full_name FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        user_name = user['full_name'] if user and user['full_name'] else email.split('@')[0]

        cur.execute("UPDATE users SET password_hash = %s WHERE email = %s", (hashed_password, email))
        mysql.connection.commit()

        # Send notification email
        try:
            send_password_reset_notification_email(email, user_name, new_password)
        except Exception as e:
            # Log the error but don't block the user-facing success message
            print(f"Password reset email sending error: {e}")

        cur.close()
        flash(f"Password for {email} reset successfully. The user has been notified.", "success")
    except MySQLdb.Error as e:
        flash(f"Error resetting password: {e}", "error")
        
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/toggle_role", methods=["POST"])
def admin_toggle_role():
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("home"))
        
    email = request.form.get('email')
    current_role = request.form.get('current_role')
    
    if email == session['user']:
        flash("You cannot change your own role.", "error")
        return redirect(url_for("admin_dashboard"))
        
    new_role = 'user' if current_role == 'admin' else 'admin'
    
    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE users SET role = %s WHERE email = %s", (new_role, email))
        mysql.connection.commit()
        cur.close()
        flash(f"Role for {email} changed to {new_role}.", "success")
    except MySQLdb.Error as e:
        flash(f"Error changing role: {e}", "error")
        
    return redirect(url_for("admin_dashboard"))


# Suspended: admin_toggle_active removed; suspend/reactivate feature cleaned up.


@app.route('/admin/flag_transaction', methods=['POST'])
def admin_flag_transaction():
    if "user" not in session or session.get("role") != "admin":
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    tx_id = request.form.get('tx_id') or request.json and request.json.get('tx_id')
    if not tx_id:
        return jsonify({'success': False, 'message': 'tx_id required'}), 400

    try:
        ensure_transaction_flags_columns()
        cur = mysql.connection.cursor()
        cur.execute("UPDATE transactions SET flagged = NOT IFNULL(flagged, FALSE) WHERE id = %s", (tx_id,))
        mysql.connection.commit()
        cur.close()
        return jsonify({'success': True, 'message': 'Transaction flag toggled.'})
    except MySQLdb.Error as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/approve_goal', methods=['POST'])
def admin_approve_goal():
    if "user" not in session or session.get("role") != "admin":
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    goal_id = request.form.get('goal_id') or request.json and request.json.get('goal_id')
    if not goal_id:
        return jsonify({'success': False, 'message': 'goal_id required'}), 400

    try:
        ensure_goal_approval_column()
        cur = mysql.connection.cursor()
        cur.execute("UPDATE goals SET approved = TRUE WHERE id = %s", (goal_id,))
        mysql.connection.commit()
        cur.close()
        return jsonify({'success': True, 'message': 'Goal approved.'})
    except MySQLdb.Error as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/export/<string:what>')
def admin_export_csv(what):
    if "user" not in session or session.get("role") != "admin":
        flash("Access denied. Admins only.", "error")
        return redirect(url_for('admin_dashboard'))

    try:
        cur = mysql.connection.cursor()
        output = io.StringIO()
        writer = csv.writer(output)

        if what == 'users':
            ensure_is_active_column()
            cur.execute("SELECT email, role, is_active FROM users")
            rows = cur.fetchall()
            writer.writerow(['email', 'role', 'is_active'])
            for r in rows:
                writer.writerow([r['email'], r.get('role'), bool(r.get('is_active'))])
            filename = 'users.csv'

        elif what == 'transactions':
            ensure_transaction_flags_columns()
            cur.execute("SELECT id, user_email, type, amount, category, date, flagged FROM transactions ORDER BY date DESC")
            rows = cur.fetchall()
            writer.writerow(['id', 'user_email', 'type', 'amount', 'category', 'date', 'flagged'])
            for r in rows:
                writer.writerow([r['id'], r['user_email'], r['type'], float(r['amount'] or 0), r['category'], r['date'], bool(r.get('flagged'))])
            filename = 'transactions.csv'

        elif what == 'goals':
            ensure_goal_approval_column()
            cur.execute("SELECT id, user_email, title, category, current_amount, target_amount, target_date, approved FROM goals ORDER BY target_date")
            rows = cur.fetchall()
            writer.writerow(['id', 'user_email', 'title', 'category', 'current_amount', 'target_amount', 'target_date', 'approved'])
            for r in rows:
                writer.writerow([r['id'], r['user_email'], r['title'], r['category'], float(r.get('current_amount') or 0), float(r.get('target_amount') or 0), r.get('target_date'), bool(r.get('approved'))])
            filename = 'goals.csv'

        else:
            flash('Export type not supported.', 'error')
            return redirect(url_for('admin_dashboard'))

        cur.close()
        resp = make_response(output.getvalue())
        resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
        resp.headers['Content-Type'] = 'text/csv'
        return resp
    except MySQLdb.Error as e:
        flash(f'Error exporting data: {e}', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/export_report')
def export_report():
    if "user" not in session:
        return redirect(url_for("home"))

    user_email = session["user"]
    try:
        cur = mysql.connection.cursor()
        
        # Fetch user details for the report header
        cur.execute("SELECT full_name, phone, currency FROM users WHERE email = %s", (user_email,))
        user_info = cur.fetchone()
        user_name = user_info.get('full_name') or user_email
        user_phone = user_info.get('phone') or "N/A"
        currency = user_info.get('currency') or '\u20b9'

        cur.execute("SELECT date, type, category, amount, description FROM transactions WHERE user_email = %s ORDER BY date DESC", (user_email,))
        transactions = cur.fetchall()
        cur.close()

        # Calculate totals
        total_income = sum(t['amount'] for t in transactions if t['type'] == 'income')
        total_expense = sum(t['amount'] for t in transactions if t['type'] == 'expense')
        balance = total_income - total_expense

        # Generate PDF
        pdf = FPDF()
        pdf.add_page()

        # Helper to handle unicode characters for PDF generation, replacing them if they can't be encoded.
        def to_pdf_str(text):
            return str(text).encode('latin-1', 'replace').decode('latin-1')
        
        # Header
        pdf.set_font("Arial", "B", 20)
        pdf.cell(0, 10, "Finance Tracker Statement", ln=True, align="C")
        pdf.ln(5)
        
        # User Info
        pdf.set_font("Arial", "", 12)
        pdf.cell(0, 7, to_pdf_str(f"Account Holder: {user_name}"), ln=True)
        pdf.cell(0, 7, to_pdf_str(f"Email: {user_email}"), ln=True)
        pdf.cell(0, 7, to_pdf_str(f"Phone: {user_phone}"), ln=True)
        pdf.cell(0, 7, f"Date: {datetime.now().strftime('%Y-%m-%d')}", ln=True)
        pdf.ln(5)
        
        # Summary
        pdf.set_font("Arial", "B", 12)
        summary_text = f"Total Income: {currency}{total_income:,.2f} | Total Expense: {currency}{total_expense:,.2f} | Balance: {currency}{balance:,.2f}"
        pdf.cell(0, 10, to_pdf_str(summary_text), ln=True, align="L")
        pdf.ln(5)
        
        # Table Header
        pdf.set_fill_color(230, 230, 230)
        pdf.set_font("Arial", "B", 10)
        pdf.cell(30, 10, "Date", 1, 0, "C", True)
        pdf.cell(25, 10, "Type", 1, 0, "C", True)
        pdf.cell(40, 10, "Category", 1, 0, "C", True)
        pdf.cell(30, 10, "Amount", 1, 0, "C", True)
        pdf.cell(65, 10, "Description", 1, 1, "C", True)
        
        # Table Rows
        pdf.set_font("Arial", "", 10)
        for t in transactions:
            pdf.cell(30, 10, str(t['date']), 1)
            pdf.cell(25, 10, to_pdf_str(t['type'].capitalize()), 1)
            pdf.cell(40, 10, to_pdf_str(t['category']), 1)
            
            # Color amount
            if t['type'] == 'income':
                pdf.set_text_color(0, 128, 0)
            else:
                pdf.set_text_color(200, 0, 0)
            pdf.cell(30, 10, to_pdf_str(f"{currency}{t['amount']:,.2f}"), 1, 0, "R")
            pdf.set_text_color(0, 0, 0)
            
            # Truncate description
            desc = t['description'] or ""
            if len(desc) > 32: desc = desc[:29] + "..."
            pdf.cell(65, 10, to_pdf_str(desc), 1, 1)

        # Output PDF
        response = make_response(pdf.output(dest='S').encode('latin-1'))
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = 'inline; filename=statement.pdf'
        return response
    except Exception as e:
        print(f"Error exporting report: {e}")
        flash("Error generating report.", "error")
        return redirect(url_for("dashboard"))

import razorpay
from config import Config

razorpay_client = razorpay.Client(auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET))
@app.route("/create-order", methods=["POST"])
def create_order():
    if "user" not in session:
        return redirect(url_for("home"))

    amount = 49900  # â‚¹499 in paise

    order = razorpay_client.order.create({
        "amount": amount,
        "currency": "INR",
        "payment_capture": 1
    })

    return jsonify(order)
@app.route("/verify-payment", methods=["POST"])
def verify_payment():
    data = request.get_json()

    try:
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': data['razorpay_order_id'],
            'razorpay_payment_id': data['razorpay_payment_id'],
            'razorpay_signature': data['razorpay_signature']
        })

        # Update DB
        cur = mysql.connection.cursor()
        cur.execute("UPDATE users SET is_paid = TRUE WHERE email = %s", (session["user"],))
        mysql.connection.commit()
        cur.close()

        session["is_paid"] = True

        return jsonify({"success": True})

    except:
        return jsonify({"success": False}), 400
def send_email(to_email, subject, body):
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = Config.EMAIL
    msg['To'] = to_email

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(Config.EMAIL, Config.EMAIL_PASS)
    server.send_message(msg)
    server.quit()
@app.route("/forgot-password", methods=["POST"])
def forgot_password():
    email = request.json.get("email")

    new_password = "Temp@1234"
    hashed = generate_password_hash(new_password)

    cur = mysql.connection.cursor()
    cur.execute("UPDATE users SET password_hash=%s WHERE email=%s", (hashed, email))
    mysql.connection.commit()
    cur.close()

    send_email(email, "Password Reset", f"New Password: {new_password}")

    return jsonify({"message": "Password sent to email"})

if __name__ == "__main__":
    app.run(debug=True)
