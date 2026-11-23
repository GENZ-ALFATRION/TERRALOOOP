# TerraLoop — Flask + Neon PostgreSQL + Vercel

TerraLoop is a Flask full-stack biowaste and biocompost platform. This version uses **Neon PostgreSQL** for persistent cloud database storage and includes a Vercel Python deployment configuration. It no longer uses a local SQLite database file.

## Included features

- Role-based accounts: college, converter, farmer/gardener, consumer and admin.
- Password hashing, session login/logout, CSRF form tokens and role-protected routes.
- College organic-waste collection requests and converter pickup workflow.
- Converter compost product listings and marketplace browsing.
- Order requests, stock updates and cancellation restoration.
- Crop/species compost requirement planner.
- Admin dashboard, configurable revenue split, college payout settings and manual payment-split ledger.

## 1. Create a Neon database

1. Sign in to [Neon](https://neon.tech/) and create a project.
2. Open the project's **Connect** panel.
3. Select the database and role you want the app to use.
4. Copy the connection string. Prefer Neon’s **pooled** connection string for a serverless deployment, and ensure it includes `sslmode=require`.

The connection string looks similar to:

```text
postgresql://USER:PASSWORD@YOUR-NEON-HOST-pooler.REGION.aws.neon.tech/DBNAME?sslmode=require
```

Keep the password private. Do not commit the real connection string to GitHub or put it in frontend code.

## 2. Configure Vercel environment variables

In Vercel, open the TerraLoop project → **Settings → Environment Variables** and add:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Full Neon PostgreSQL pooled connection string |
| `SECRET_KEY` | A long, random secret (not the example value) |
| `ADMIN_EMAIL` | Email address that should receive the admin role when registering |

Apply the variables to the Production environment (and Preview if needed), save, then redeploy the project. `DATABASE_URL` is required; the app will show a clear server error if it is missing.

## 3. Deploy

Push this folder's contents to the GitHub repository connected to Vercel, or import the repository into Vercel. The included `vercel.json` routes requests to the Flask application in `app.py`. Vercel installs packages from `requirements.txt`.

The app creates its PostgreSQL tables automatically when the first request reaches it. Tables are created with `IF NOT EXISTS`, so existing Neon tables are not overwritten.

## 4. Run locally

Requires Python 3.10+ and a Neon connection string.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

Set environment variables before starting (use your own values):

```bash
export DATABASE_URL='postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require'
export SECRET_KEY='a-long-random-secret'
export ADMIN_EMAIL='admin@example.com'
python app.py
```

Open `http://127.0.0.1:5000`.

## Database and existing data

The source project used SQLite. This updated project switches its runtime schema and queries to PostgreSQL. The uploaded ZIP did **not** contain a `terraloop.db` SQLite database file, so no existing user accounts, products, orders or pickup records could be copied into Neon as part of this code conversion. The PostgreSQL tables will start empty. If you have the old `.db` file, keep it and migrate its records separately before directing real users to the new database.

## Important production notes

- Set a strong `SECRET_KEY`; do not rely on the local-development fallback in production.
- Use Neon’s pooled connection string for Vercel serverless traffic.
- Back up the Neon database before major schema changes.
- This prototype records order totals and proposed payment splits; it does not collect payments or transfer funds.
- Before commercial use, add email verification, password reset, rate limiting, audit logs, privacy/terms, database migrations and transaction/concurrency tests.
