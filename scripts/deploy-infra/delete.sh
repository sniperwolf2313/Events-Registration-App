#!/usr/bin/env bash
set -euo pipefail
: "${AWS_REGION:=us-east-1}"
: "${PROJECT_NAME:=event-manager}"
: "${ENVIRONMENT:=dev}"

for suffix in compute-api messaging auth data storage; do
  stack="${PROJECT_NAME}-${ENVIRONMENT}-${suffix}"
  echo "Deleting ${stack}"
  aws cloudformation delete-stack --stack-name "${stack}" --region "${AWS_REGION}" || true
  aws cloudformation wait stack-delete-complete --stack-name "${stack}" --region "${AWS_REGION}" || true
done
