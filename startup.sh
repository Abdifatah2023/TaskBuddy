#!/bin/bash
set -e

# Write Google token/credential files from HF Space secrets (env vars) to /etc/secrets/
# The app already checks /etc/secrets/ when running on a hosted platform.

if [ -n "$CALENDAR_TOKEN" ]; then
    echo "$CALENDAR_TOKEN" > /etc/secrets/Calendar_token.json
fi

if [ -n "$DRIVE_TOKEN" ]; then
    echo "$DRIVE_TOKEN" > /etc/secrets/drive_token.json
fi

if [ -n "$EMAIL_TOKEN" ]; then
    echo "$EMAIL_TOKEN" > /etc/secrets/email_token.json
fi

if [ -n "$CREDENTIALS_JSON" ]; then
    echo "$CREDENTIALS_JSON" > /etc/secrets/credentials.json
    # App references ./credentials.json (relative to WORKDIR /app)
    echo "$CREDENTIALS_JSON" > /app/credentials.json
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 7860
