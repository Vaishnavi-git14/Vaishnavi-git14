import os
from flask import Flask, render_template, request, redirect, session, flash
from flask_wtf.csrf import CSRFProtect
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
# FIX: Secure random secret key (no longer hardcoded)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# FIX: CSRF protection enabled globally
csrf = CSRFProtect(app)

# ─── Database helper (per-request connection, no global cursor) ────────────────
def get_db():
    db = mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="expense_db"
    )
    return db

# ─── Home ──────────────────────────────────────────────────────────────────────
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect('/dashboard')
    return redirect('/login')

# ─── Register ─────────────────────────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        # Input validation
        if len(username) < 3:
            flash("Username must be at least 3 characters.", "error")
            return render_template('register.html')
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template('register.html')

        hashed_password = generate_password_hash(password)

        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO users(username, password) VALUES(%s, %s)",
                (username, hashed_password)
            )
            db.commit()
            cursor.close()
            db.close()
            flash("Registration successful! Please login.", "success")
            return redirect('/login')
        except mysql.connector.IntegrityError:
            flash("Username already exists. Try a different one.", "error")
        except Exception as e:
            flash("An error occurred. Please try again.", "error")

    return render_template('register.html')

# ─── Login ────────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "SELECT id, password FROM users WHERE username=%s",
                (username,)
            )
            user = cursor.fetchone()
            cursor.close()
            db.close()

            if user and check_password_hash(user[1], password):
                session['user_id'] = user[0]
                session['username'] = username
                return redirect('/dashboard')
            else:
                flash("Invalid username or password.", "error")
        except Exception as e:
            flash("Database error. Please try again.", "error")

    return render_template('login.html')

# ─── Dashboard ────────────────────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect('/login')

    # Optional month/year filter from query params
    month = request.args.get('month', '')
    year  = request.args.get('year', '')

    try:
        db = get_db()
        cursor = db.cursor()

        base_query = """
            SELECT id, title, amount, type, category, trans_date
            FROM transactions
            WHERE user_id=%s
        """
        params = [session['user_id']]

        if month and year:
            base_query += " AND MONTH(trans_date)=%s AND YEAR(trans_date)=%s"
            params += [month, year]
        elif year:
            base_query += " AND YEAR(trans_date)=%s"
            params.append(year)

        base_query += " ORDER BY trans_date DESC"

        cursor.execute(base_query, params)
        transactions = cursor.fetchall()

        # Compute totals
        total_expense = sum(float(t[2]) for t in transactions if t[3] == 'Expense')
        total_income  = sum(float(t[2]) for t in transactions if t[3] == 'Income')
        balance = total_income - total_expense

        # Category breakdown for chart (expenses only)
        cat_query = """
            SELECT category, SUM(amount)
            FROM transactions
            WHERE user_id=%s AND type='Expense'
        """
        cat_params = [session['user_id']]
        if month and year:
            cat_query += " AND MONTH(trans_date)=%s AND YEAR(trans_date)=%s"
            cat_params += [month, year]
        cat_query += " GROUP BY category"
        cursor.execute(cat_query, cat_params)
        category_data = cursor.fetchall()

        cursor.close()
        db.close()

        categories  = [row[0] for row in category_data]
        cat_amounts = [float(row[1]) for row in category_data]

    except Exception as e:
        flash("Error loading dashboard data.", "error")
        transactions = []
        total_expense, total_income, balance = 0, 0, 0
        categories, cat_amounts = [], []

    return render_template(
        'dashboard.html',
        transactions=transactions,
        total_expense=total_expense,
        total_income=total_income,
        balance=balance,
        categories=categories,
        cat_amounts=cat_amounts,
        selected_month=month,
        selected_year=year,
        username=session.get('username', 'User')
    )

# ─── Add Transaction (Expense OR Income) ──────────────────────────────────────
@app.route('/add', methods=['GET', 'POST'])
def add_expense():
    if 'user_id' not in session:
        return redirect('/login')

    if request.method == 'POST':
        title      = request.form['title'].strip()
        category   = request.form['category'].strip()
        trans_date = request.form['trans_date']
        trans_type = request.form.get('type', 'Expense')  # Income or Expense

        # Server-side input validation
        try:
            amount = float(request.form['amount'])
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash("Amount must be a positive number.", "error")
            return render_template('add_expense.html')

        if not title:
            flash("Title cannot be empty.", "error")
            return render_template('add_expense.html')

        if trans_type not in ('Income', 'Expense'):
            flash("Invalid transaction type.", "error")
            return render_template('add_expense.html')

        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute("""
                INSERT INTO transactions
                (user_id, title, amount, type, category, trans_date)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (session['user_id'], title, amount, trans_type, category, trans_date))
            db.commit()
            cursor.close()
            db.close()
            flash(f"{trans_type} added successfully!", "success")
            return redirect('/dashboard')
        except Exception as e:
            flash("Error saving transaction.", "error")

    return render_template('add_expense.html')

# ─── Delete Transaction (FIX: POST only, no accidental GET deletes) ───────────
@app.route('/delete/<int:id>', methods=['POST'])
def delete_expense(id):
    if 'user_id' not in session:
        return redirect('/login')

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "DELETE FROM transactions WHERE id=%s AND user_id=%s",
            (id, session['user_id'])
        )
        db.commit()
        cursor.close()
        db.close()
        flash("Transaction deleted.", "success")
    except Exception as e:
        flash("Error deleting transaction.", "error")

    return redirect('/dashboard')

# ─── Logout ───────────────────────────────────────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

if __name__ == '__main__':
    app.run(debug=True)