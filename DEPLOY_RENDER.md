# Put Powder Inventory on demiralays.com

This is the hosted edition of Powder Inventory v8.0. It runs as its own Render
Web Service at the proposed address `powder.demiralays.com`, using a separate
private GitHub repository and its own login. The existing H2Glass service and
DNS record do not need to change. The inventory models and default simulation
settings are the same as the desktop edition.

## 1. Create a private repository

Create a **private** GitHub repository, for example `powder-inventory`.
Upload the files inside the extracted directory so that `render.yaml`,
`requirements.txt` and `gunicorn.conf.py` are at the repository root.
Keep local spreadsheets, scenario exports and environment secrets out of Git.

## 2. Deploy the service

In Render, choose **New → Blueprint** and connect the new private repository.
The Blueprint requests a Free Python web service in Frankfurt and configures:

| Setting | Value |
| --- | --- |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn -c gunicorn.conf.py` |
| Health check path | `/healthz` |
| Custom hostname used by the app | `powder.demiralays.com` |

When prompted, set `POWDER_USERNAME` and a separate, strong
`POWDER_PASSWORD` (16–256 characters). Render generates
`POWDER_SECRET_KEY` when using the Blueprint. If creating the Web Service
manually instead, set these three environment variables and `POWDER_DOMAIN`;
generate the signing secret locally with
`python -c "import secrets; print(secrets.token_hex(32))"`.
Set passwords and secrets in the Render dashboard, never in GitHub or chat.
The service refuses to start if the login configuration is missing.

Open the Render-assigned `.onrender.com` HTTPS URL first and verify the login,
single scenario, and multiple-scenario screens. Run a bundled sample.

## 3. Connect the domain

In the new service, add `powder.demiralays.com` under **Settings → Custom
Domains**. In GoDaddy, add a **CNAME** record with name `powder` and value
equal to the exact `.onrender.com` hostname assigned to the new service,
without a scheme or path. Return to Render and verify the domain. Once
HTTPS is ready, repeat the sign-in and sample checks on the custom address.
If desired, add a link to this HTTPS address from your website.

The Free instance can take about a minute to wake after inactivity. A
stochastic run can take longer on hosted hardware than on your desktop.
This edition runs one simulation or workbook import at a time. It does not
store submitted scenarios or results in a database; export results before
leaving the app. Sign in again if the eight-hour session expires.

- [Render custom domain DNS guide](https://render.com/docs/configure-other-dns)
- [Render Free instance behavior](https://render.com/docs/free)
