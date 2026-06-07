#!/bin/sh
# Replace the placeholder API URL in the built JS bundle with the runtime env var
# This allows VITE_API_URL to be set as a Railway runtime variable, not just build arg

if [ -n "$VITE_API_URL" ]; then
  echo "[entrypoint] Replacing API URL with: $VITE_API_URL"
  find /usr/share/nginx/html/assets -name "*.js" -exec \
    sed -i "s|http://localhost:8000|$VITE_API_URL|g" {} \;
else
  echo "[entrypoint] VITE_API_URL not set, using build-time default"
fi
