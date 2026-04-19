#!/bin/bash

# Check if the port is provided as an argument
if [ -z "$1" ]; then
  echo "Error: No port provided. Please specify the port as the first argument."
  exit 1
fi

# Assign the port passed as an argument to the PORT variable
PORT=$1

# Use regex to match http://localhost with optional existing port (:XXXX)
# This prevents double-port concatenation (http://localhost:XXXX:XXXX)
wp search-replace 'http://localhost[:0-9]*' "http://localhost:${PORT}" \
  --all-tables --regex --path=/opt/bitnami/wordpress

# Clear the cache
wp cache flush --path=/opt/bitnami/wordpress

echo "URLs updated to port ${PORT}"
