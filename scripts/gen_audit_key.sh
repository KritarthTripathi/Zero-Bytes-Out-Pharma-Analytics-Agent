#!/bin/sh
# Creates the audit log signing key (docker secret). Keep it out of git. Losing it means old audit records can no longer be verified.
mkdir -p secrets
if [ -f secrets/audit_hmac_key ]; then echo "secrets/audit_hmac_key already exists (not replaced)"; else
  python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/audit_hmac_key && chmod 600 secrets/audit_hmac_key && echo "wrote secrets/audit_hmac_key"
fi
