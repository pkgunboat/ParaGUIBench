#!/bin/bash

# Check if both URLs are provided as arguments
if [ -z "$1" ] || [ -z "$2" ]; then
  echo "Error: Missing arguments. Please provide both source and target URLs."
  echo "Usage: $0 <source_url> <target_url>"
  echo "Example: $0 'http://localhost:8083' 'https://webmall-3.informatik.uni-mannheim.de/'"
  exit 1
fi

# Assign the URLs passed as arguments
SOURCE_URL=$1
TARGET_URL=$2

# Perform search-replace on the URLs
wp search-replace "$SOURCE_URL" "$TARGET_URL" --all-tables --path=/opt/bitnami/wordpress

# Clear the cache
wp cache flush --path=/opt/bitnami/wordpress

echo "URLs updated from ${SOURCE_URL} to ${TARGET_URL}"


