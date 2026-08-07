# BC Branded HTML Publisher — Temporary Login Version

This version uses an explicit Better Collective email allowlist plus an individual passcode for each approved user.

Approved users:

- Gautham
- Ben
- Kathy
- Tobias
- Devang
- Gerry
- Bruno
- Nicholas

The email/name/passcode mapping lives only in Streamlit Secrets.

## Login

Example:

```text
Better Collective email
bmendelowitz@bettercollective.com

Passcode
••••••••

[ Sign in ]
```

After login:

```text
Welcome, Ben
bmendelowitz@bettercollective.com
```

Only emails explicitly configured under `[users]` can authenticate.

## Important limitation

This is a temporary authentication system.

It proves that the person knows the passcode associated with the approved email. It does NOT independently verify ownership of the Better Collective email account.

When Better Collective OneLogin OIDC access is available, replace this login with OneLogin SSO. At that point OneLogin will verify the employee's identity directly.

## GitHub structure

```text
bc-branded-html-publisher/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    └── secrets.example.toml
```

Do not commit `.streamlit/secrets.toml`.

## Streamlit Cloud

Deploy `app.py`, then open:

`App -> Settings -> Secrets`

Paste the contents of `secrets.example.toml`, replacing all placeholder passcodes and GitHub credentials.

Give every approved employee a different private passcode.

## Repository naming

Format:

`brand-page-name-DDMMYYHHMM`

Example:

`action-network-nfl-stadium-costs-0708261147`

Time zone: Europe/London.
