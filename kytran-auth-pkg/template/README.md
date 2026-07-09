# KytranAuth Template — Drop-In Auth for Flask Standalones

## Files to Copy

1. `kytran_auth.py` — OAuth SDK (from /mnt/archie_brain/kytran-auth/)
2. `auth.py` — Tier decorators, /api/me, /auth/logout
3. `landing.html` — Parameterized LCARS landing page (copy to templates/)

## Integration Steps

### 1. Copy files
```bash
cp /mnt/archie_brain/kytran-auth/kytran_auth.py /path/to/product/
cp /mnt/archie_brain/kytran-auth/template/auth.py /path/to/product/
cp /mnt/archie_brain/kytran-auth/template/landing.html /path/to/product/templates/
```

### 2. Wire into app.py
```python
# Session config
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# KytranAuth SDK
from kytran_auth import KytranAuth
from auth import auth_bp

kytran_auth = KytranAuth()
app.config["KYTRAN_CLIENT_ID"] = os.environ.get("KYTRAN_CLIENT_ID", "product-name")
kytran_auth.init_app(app)

app.register_blueprint(auth_bp)

# Landing page (public)
@app.route("/")
def landing():
    return render_template("landing.html",
        product_name="Product Name",
        product_short="P.N.",
        product_name_html="KYTRAN <span>PRODUCT</span>",
        product_description="Description here.",
        accent_color="#00e5ff",
        features=[
            {"title": "FEATURE 1", "description": "What it does."},
            {"title": "FEATURE 2", "description": "What it does."},
        ],
    )

# Dashboard (authenticated)
@app.route("/dashboard")
def dashboard():
    if "kytran_user" not in session:
        return redirect("/auth/kytran/login?next=/dashboard")
    return send_from_directory("static", "index.html")
```

### 3. Protect routes
```python
from auth import tier_required, admin_required

@bp.route("/api/data")
@tier_required("viewer")
def get_data():
    ...
```

### 4. Register OAuth client on ARCHIE
```sql
INSERT INTO oauth_clients (client_id, client_secret, redirect_uri, name)
VALUES ('product-name', '<secret>', 'https://product.kytranempowerment.com/auth/kytran/callback', 'Product Name');
```

### 5. Add env vars to docker-compose.yml
```yaml
environment:
  - SECRET_KEY=${SECRET_KEY:-dev-secret}
  - KYTRAN_CLIENT_ID=product-name
  - KYTRAN_CLIENT_SECRET=${KYTRAN_CLIENT_SECRET:-}
  - KYTRAN_AUTH_URL=${KYTRAN_AUTH_URL:-http://192.168.1.200:3000}
  - KYTRAN_REDIRECT_URI=${KYTRAN_REDIRECT_URI:-https://product.kytranempowerment.com/auth/kytran/callback}
```
