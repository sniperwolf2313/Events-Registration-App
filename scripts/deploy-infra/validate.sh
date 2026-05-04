#!/usr/bin/env bash
set -euo pipefail
: "${AWS_REGION:=us-east-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

for template in "${ROOT_DIR}"/infra/*.yaml; do
  echo "Validating ${template}"
  aws cloudformation validate-template --template-body "file://${template}" --region "${AWS_REGION}" >/dev/null
  echo "OK: ${template}"
done
