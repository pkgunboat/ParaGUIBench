#!/bin/bash

# Check if the port is provided as an argument
if [ -z "$1" ]; then
  echo "Error: No port provided. Please specify the port as the first argument."
  exit 1
fi

# Assign the port passed as an argument to the PORT variable
PORT=$1

# Perform search-replace on the URLs
# WARNING!: This search is not looking for exactly this string, so http://localhost:XXXX gets turned to http://localhost:XXXX:XXXX
wp search-replace 'http://localhost' "http://localhost:${PORT}" --all-tables --path=/opt/bitnami/wordpress

# Clear the cache
wp cache flush --path=/opt/bitnami/wordpress

echo "URLs updated to port ${PORT}"

