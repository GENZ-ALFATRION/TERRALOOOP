import os, secrets
import psycopg
from psycopg.rows import dict_row
from functools import wraps
from datetime import datetime
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or 'local-development-only-' + secrets.token_hex(16)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.environ.get('VERCEL') == '1')
app.config['DATABASE_URL'] = os.environ.get('DATABASE_URL', '').strip()
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', '').strip().lower()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('college','converter','farmer','consumer','admin')),
 organization TEXT DEFAULT '', phone TEXT DEFAULT '', address TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
 id BIGSERIAL PRIMARY KEY, seller_id BIGINT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
 compost_type TEXT NOT NULL, description TEXT DEFAULT '', quantity_kg REAL NOT NULL CHECK(quantity_kg >= 0),
 price_per_kg REAL NOT NULL CHECK(price_per_kg >= 0), location TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS needs (
 id BIGSERIAL PRIMARY KEY, buyer_id BIGINT NOT NULL REFERENCES users(id), species TEXT NOT NULL,
 area_m2 REAL NOT NULL CHECK(area_m2 > 0), rate_kg_m2 REAL NOT NULL, estimated_kg REAL NOT NULL,
 notes TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
 id BIGSERIAL PRIMARY KEY, product_id BIGINT NOT NULL REFERENCES products(id), buyer_id BIGINT NOT NULL REFERENCES users(id),
 seller_id BIGINT NOT NULL REFERENCES users(id), quantity_kg REAL NOT NULL, total_amount REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'requested', payment_status TEXT NOT NULL DEFAULT 'not_paid', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS waste_pickups (
 id BIGSERIAL PRIMARY KEY, college_id BIGINT NOT NULL REFERENCES users(id), converter_id BIGINT REFERENCES users(id),
 waste_type TEXT NOT NULL, quantity_kg REAL NOT NULL CHECK(quantity_kg > 0), pickup_date TEXT DEFAULT '', notes TEXT DEFAULT '',
 status TEXT NOT NULL DEFAULT 'requested', college_payout REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payment_splits (
 id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL REFERENCES orders(id), gross_amount REAL NOT NULL,
 platform_pct REAL NOT NULL, converter_pct REAL NOT NULL, college_pct REAL NOT NULL,
 platform_amount REAL NOT NULL, converter_amount REAL NOT NULL, college_amount REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending_manual', created_at TEXT NOT NULL
);
'''


# Practical planning guide: rates are indicative, not a substitute for soil testing.
def compost_guidance(species):
    name = species.lower().strip()
    groups = [
        (("tomato", "brinjal", "eggplant", "chilli", "pepper", "okra", "ladyfinger", "cucumber", "cabbage", "cauliflower", "spinach", "lettuce", "vegetable"), 3.0, "Mature compost + vermicompost", "Use well-matured, screened compost as the base; add a smaller vermicompost portion near planting for nutrient availability."),
        (("rose", "marigold", "hibiscus", "jasmine", "bougainvillea", "chrysanthemum", "flower"), 2.5, "Mature compost + vermicompost", "Blend mature compost into the bed; use vermicompost sparingly around established plants."),
        (("mango", "guava", "lemon", "citrus", "orange", "pomegranate", "apple", "papaya", "banana", "fruit tree", "orchard"), 4.0, "Mature farmyard compost / tree compost", "Apply in a broad ring beneath the canopy rather than against the trunk; adjust by tree age and canopy size."),
        (("wheat", "rice", "paddy", "maize", "corn", "millet", "sorghum", "barley", "cereal"), 2.0, "Mature farmyard compost", "Incorporate mature compost before sowing; soil-test-based nutrient planning is especially important for field crops."),
        (("gram", "chickpea", "lentil", "pea", "beans", "soybean", "groundnut", "legume", "pulse"), 1.5, "Mature compost", "Use a modest basal application and avoid excessive nitrogen-rich inputs."),
        (("tulsi", "basil", "mint", "coriander", "parsley", "herb"), 2.0, "Mature compost / leaf compost", "Use a light, well-finished compost; avoid fresh or hot manure around edible leaves."),
        (("lawn", "grass", "turf"), 1.5, "Fine screened compost", "Top-dress thinly and evenly; avoid burying grass blades."),
        (("indoor", "money plant", "pothos", "snake plant", "succulent", "cactus", "houseplant"), 0.5, "Leaf mould + mature compost", "Use a low proportion of compost in a free-draining potting mix; container plants are sensitive to dense, wet mixes."),
    ]
    for keywords, rate, compost, note in groups:
        if any(k in name for k in keywords):
            return rate, compost, note
    return 2.0, "Mature mixed organic compost", "General starting estimate for an established planting bed; confirm the rate for the exact species and soil before large-scale application."

class Database:
    """Small adapter preserving the app's parameterized execute() interface."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=None):
        # Translate the SQLite placeholder style used by the existing routes.
        return self.connection.execute(sql.replace('?', '%s'), params or ())

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def db():
    if 'db' not in g:
        database_url = app.config['DATABASE_URL']
        if not database_url:
            raise RuntimeError('DATABASE_URL is not set. Add your Neon PostgreSQL connection string in Vercel Environment Variables.')
        connection = psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10)
        g.db = Database(connection)
    return g.db

@app.teardown_appcontext
def close_db(exc):
    conn = g.pop('db', None)
    if conn is not None: conn.close()

@app.before_request
def init_db_and_csrf():
    for statement in SCHEMA.split(';'):
        if statement.strip():
            db().execute(statement)
    for key, value in [('platform_pct','0'),('converter_pct','0'),('college_pct','0'),('college_payout_per_kg','0')]:
        db().execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING',(key,value))
    db().commit()
    if 'csrf' not in session: session['csrf'] = secrets.token_urlsafe(24)

@app.context_processor
def inject_globals():
    return {'current_user': current_user(), 'csrf_token': session.get('csrf','')}

def current_user():
    uid = session.get('user_id')
    return db().execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone() if uid else None

def login_required(fn):
    @wraps(fn)
    def wrapped(*a,**kw):
        if not current_user(): flash('Please log in to continue.','warning'); return redirect(url_for('login'))
        return fn(*a,**kw)
    return wrapped

def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapped(*a,**kw):
            if current_user()['role'] not in roles: abort(403)
            return fn(*a,**kw)
        return wrapped
    return deco

def valid_csrf():
    if request.form.get('csrf_token') != session.get('csrf'): abort(400, 'Invalid form token. Refresh and try again.')

def setting(key):
    row=db().execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return float(row['value']) if row else 0.0

def stamp(): return datetime.utcnow().isoformat(timespec='seconds')+'Z'

@app.route('/')
def home():
    products=db().execute("SELECT p.*,u.name seller_name FROM products p JOIN users u ON u.id=p.seller_id WHERE p.status='active' ORDER BY p.created_at DESC LIMIT 6").fetchall()
    stats={ 'products':db().execute("SELECT COUNT(*) n FROM products WHERE status='active'").fetchone()['n'],
            'colleges':db().execute("SELECT COUNT(*) n FROM users WHERE role='college'").fetchone()['n'],
            'needs':db().execute("SELECT COUNT(*) n FROM needs WHERE status='open'").fetchone()['n'] }
    return render_template('home.html',products=products,stats=stats)

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='POST':
        valid_csrf(); name=request.form.get('name','').strip(); email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
        role=request.form.get('role',''); organization=request.form.get('organization','').strip(); phone=request.form.get('phone','').strip(); address=request.form.get('address','').strip()
        if not name or not email or len(password)<8 or role not in ['college','converter','farmer','consumer']:
            flash('Enter all required details. Password must be at least 8 characters.','error')
        else:
            if ADMIN_EMAIL and email==ADMIN_EMAIL: role='admin'
            try:
                cur=db().execute('INSERT INTO users(name,email,password_hash,role,organization,phone,address,created_at) VALUES(?,?,?,?,?,?,?,?) RETURNING id',
                    (name,email,generate_password_hash(password),role,organization,phone,address,stamp())); new_user_id=cur.fetchone()['id']; db().commit()
                session['user_id']=new_user_id; flash('Your TerraLoop account is ready.','success'); return redirect(url_for('dashboard'))
            except psycopg.IntegrityError: flash('That email is already registered. Please log in.','error')
    return render_template('auth.html',mode='register')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        valid_csrf(); email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
        user=db().execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        if user and check_password_hash(user['password_hash'],password):
            session.clear(); session['user_id']=user['id']; session['csrf']=secrets.token_urlsafe(24); return redirect(url_for('dashboard'))
        flash('Email or password was not recognised.','error')
    return render_template('auth.html',mode='login')

@app.post('/logout')
@login_required
def logout(): valid_csrf(); session.clear(); flash('You have been logged out.','success'); return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    u=current_user(); role=u['role']; uid=u['id']; data={}
    if role=='converter':
        data['products']=db().execute('SELECT * FROM products WHERE seller_id=? ORDER BY id DESC',(uid,)).fetchall()
        data['orders']=db().execute('SELECT o.*,p.name product_name,b.name buyer_name FROM orders o JOIN products p ON p.id=o.product_id JOIN users b ON b.id=o.buyer_id WHERE o.seller_id=? ORDER BY o.id DESC',(uid,)).fetchall()
        data['pickups']=db().execute("SELECT w.*,u.name college_name FROM waste_pickups w JOIN users u ON u.id=w.college_id WHERE w.converter_id=? OR (w.converter_id IS NULL AND w.status='requested') ORDER BY w.id DESC",(uid,)).fetchall()
    elif role in ('farmer','consumer'):
        data['needs']=[dict(n, compost_type=compost_guidance(n['species'])[1], guidance=compost_guidance(n['species'])[2]) for n in db().execute('SELECT * FROM needs WHERE buyer_id=? ORDER BY id DESC',(uid,)).fetchall()]
        data['orders']=db().execute('SELECT o.*,p.name product_name,s.name seller_name FROM orders o JOIN products p ON p.id=o.product_id JOIN users s ON s.id=o.seller_id WHERE o.buyer_id=? ORDER BY o.id DESC',(uid,)).fetchall()
    elif role=='college':
        data['pickups']=db().execute('SELECT w.*,u.name converter_name FROM waste_pickups w LEFT JOIN users u ON u.id=w.converter_id WHERE w.college_id=? ORDER BY w.id DESC',(uid,)).fetchall()
    elif role=='admin':
        data['users']=db().execute('SELECT id,name,email,role,created_at FROM users ORDER BY id DESC').fetchall()
        data['orders']=db().execute('SELECT o.*,p.name product_name,b.name buyer_name,s.name seller_name FROM orders o JOIN products p ON p.id=o.product_id JOIN users b ON b.id=o.buyer_id JOIN users s ON s.id=o.seller_id ORDER BY o.id DESC').fetchall()
        data['splits']=db().execute('SELECT * FROM payment_splits ORDER BY id DESC').fetchall()
        data['settings']={k:setting(k) for k in ['platform_pct','converter_pct','college_pct','college_payout_per_kg']}
    return render_template('dashboard.html',data=data)

@app.route('/products')
def products():
    rows=db().execute("SELECT p.*,u.name seller_name,u.organization FROM products p JOIN users u ON u.id=p.seller_id WHERE p.status='active' AND p.quantity_kg>0 ORDER BY p.created_at DESC").fetchall()
    return render_template('products.html',products=rows)

@app.route('/products/new',methods=['GET','POST'])
@roles_required('converter')
def product_new():
    if request.method=='POST':
        valid_csrf(); f=request.form
        try:
            qty=float(f.get('quantity_kg','0')); price=float(f.get('price_per_kg','0'))
            if qty<=0 or price<0: raise ValueError()
            db().execute('INSERT INTO products(seller_id,name,compost_type,description,quantity_kg,price_per_kg,location,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (current_user()['id'],f.get('name','').strip(),f.get('compost_type','').strip(),f.get('description','').strip(),qty,price,f.get('location','').strip(),stamp())); db().commit()
            flash('Product listed in the marketplace.','success'); return redirect(url_for('dashboard'))
        except ValueError: flash('Enter a valid positive stock quantity and a non-negative price.','error')
    return render_template('product_form.html')

@app.post('/products/<int:pid>/order')
@roles_required('farmer','consumer')
def order_product(pid):
    valid_csrf(); p=db().execute("SELECT * FROM products WHERE id=? AND status='active'",(pid,)).fetchone()
    if not p: abort(404)
    try: qty=float(request.form.get('quantity_kg','0'))
    except ValueError: qty=0
    if qty<=0 or qty>p['quantity_kg']: flash('Choose a quantity available in stock.','error')
    else:
        total=round(qty*p['price_per_kg'],2)
        db().execute('INSERT INTO orders(product_id,buyer_id,seller_id,quantity_kg,total_amount,created_at) VALUES(?,?,?,?,?,?)',(pid,current_user()['id'],p['seller_id'],qty,total,stamp()))
        db().execute('UPDATE products SET quantity_kg=quantity_kg-? WHERE id=?',(qty,pid)); db().commit(); flash('Order request sent to the converter. Payment is not collected in this prototype.','success')
    return redirect(url_for('products'))

@app.route('/needs/new',methods=['GET','POST'])
@roles_required('farmer','consumer')
def need_new():
    if request.method=='POST':
        valid_csrf(); f=request.form
        try:
            area=float(f.get('area_m2','0'))
            species=f.get('species','').strip()
            if area<=0 or not species: raise ValueError()
            rate, compost_type, guidance = compost_guidance(species)
            db().execute('INSERT INTO needs(buyer_id,species,area_m2,rate_kg_m2,estimated_kg,notes,created_at) VALUES(?,?,?,?,?,?,?)',(current_user()['id'],species,area,rate,area*rate,f.get('notes','').strip(),stamp())); db().commit()
            flash(f'Need saved. Estimated compost: {area*rate:,.1f} kg ({compost_type}).','success'); return redirect(url_for('dashboard'))
        except ValueError: flash('Enter a species, a positive area and a valid application rate.','error')
    return render_template('need_form.html')

@app.post('/orders/<int:oid>/status')
@roles_required('converter','admin')
def order_status(oid):
    valid_csrf(); status=request.form.get('status')
    if status not in ['accepted','fulfilled','cancelled']: abort(400)
    order=db().execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    if not order or (current_user()['role']!='admin' and order['seller_id']!=current_user()['id']): abort(403)
    db().execute('UPDATE orders SET status=? WHERE id=?',(status,oid))
    if status=='cancelled': db().execute('UPDATE products SET quantity_kg=quantity_kg+? WHERE id=?',(order['quantity_kg'],order['product_id']))
    if status in ['accepted','fulfilled'] and not db().execute('SELECT id FROM payment_splits WHERE order_id=?',(oid,)).fetchone():
        pp,cp,clp=setting('platform_pct'),setting('converter_pct'),setting('college_pct')
        gross=order['total_amount']; db().execute('INSERT INTO payment_splits(order_id,gross_amount,platform_pct,converter_pct,college_pct,platform_amount,converter_amount,college_amount,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
          (oid,gross,pp,cp,clp,gross*pp/100,gross*cp/100,gross*clp/100,stamp()))
    db().commit(); flash('Order status updated.','success'); return redirect(url_for('dashboard'))

@app.route('/pickups/new',methods=['GET','POST'])
@roles_required('college')
def pickup_new():
    if request.method=='POST':
        valid_csrf(); f=request.form
        try: qty=float(f.get('quantity_kg','0'))
        except ValueError: qty=0
        if qty<=0 or not f.get('waste_type','').strip(): flash('Enter waste type and a positive quantity.','error')
        else:
            payout=qty*setting('college_payout_per_kg')
            db().execute('INSERT INTO waste_pickups(college_id,waste_type,quantity_kg,pickup_date,notes,college_payout,created_at) VALUES(?,?,?,?,?,?,?)',(current_user()['id'],f.get('waste_type').strip(),qty,f.get('pickup_date',''),f.get('notes','').strip(),payout,stamp())); db().commit()
            flash('Biowaste collection request submitted.','success'); return redirect(url_for('dashboard'))
    return render_template('pickup_form.html')

@app.post('/pickups/<int:wid>/accept')
@roles_required('converter','admin')
def pickup_accept(wid):
    valid_csrf(); w=db().execute('SELECT * FROM waste_pickups WHERE id=?',(wid,)).fetchone()
    if not w: abort(404)
    db().execute("UPDATE waste_pickups SET converter_id=?,status='scheduled' WHERE id=? AND status='requested'",(current_user()['id'],wid)); db().commit(); flash('Collection request assigned to your converter account.','success'); return redirect(url_for('dashboard'))

@app.post('/pickups/<int:wid>/status')
@roles_required('converter','admin')
def pickup_status(wid):
    valid_csrf(); status=request.form.get('status')
    if status not in ['collected','converted','cancelled']: abort(400)
    w=db().execute('SELECT * FROM waste_pickups WHERE id=?',(wid,)).fetchone()
    if not w or (current_user()['role']!='admin' and w['converter_id']!=current_user()['id']): abort(403)
    db().execute('UPDATE waste_pickups SET status=? WHERE id=?',(status,wid)); db().commit(); flash('Collection status updated.','success'); return redirect(url_for('dashboard'))

@app.route('/admin/settings',methods=['POST'])
@roles_required('admin')
def admin_settings():
    valid_csrf(); f=request.form
    try:
        vals={k:float(f.get(k,'0')) for k in ['platform_pct','converter_pct','college_pct','college_payout_per_kg']}
        if min(vals.values())<0 or sum(vals[k] for k in ['platform_pct','converter_pct','college_pct'])>100: raise ValueError()
        for k,v in vals.items(): db().execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value',(k,str(v)))
        db().commit(); flash('Marketplace split and college waste payout settings saved.','success')
    except ValueError: flash('Percentages must be non-negative and the three-way split cannot exceed 100%.','error')
    return redirect(url_for('dashboard'))

@app.errorhandler(403)
def forbidden(e): return render_template('error.html',code=403,message='You do not have permission to view this page.'),403
@app.errorhandler(404)
def not_found(e): return render_template('error.html',code=404,message='That page or record could not be found.'),404

if __name__=='__main__': app.run(debug=os.environ.get('FLASK_DEBUG')=='1',host='127.0.0.1',port=int(os.environ.get('PORT',5000)))
