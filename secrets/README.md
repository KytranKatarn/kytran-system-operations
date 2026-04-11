# Secrets

This directory holds Docker secrets mounted read-only into containers.

## Setup
```bash
openssl rand -base64 32 > secrets/db_password.txt
chmod 600 secrets/db_password.txt
```

`secrets/db_password.txt` is gitignored — NEVER commit it.
