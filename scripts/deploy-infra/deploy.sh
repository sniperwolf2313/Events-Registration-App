#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${PROJECT_NAME:=event-manager}"
: "${ENVIRONMENT:=dev}"
: "${SENDER_EMAIL:=sniperwolf2313@gmail.com}"
: "${CALLBACK_URL:=http://localhost:3000/callback}"
: "${LOGOUT_URL:=http://localhost:3000/logout}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"


echo "Desplegando infra 01-storage.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/01-storage.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-storage" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" \
  --region "${AWS_REGION}"


echo "Creating and uploading bootstrap Lambda artifact..."

LAMBDA_ARTIFACTS_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-storage \
  --region ${AWS_REGION} \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendArtifactsBucketName'].OutputValue | [0]" \
  --output text)

mkdir -p .build/dummy-lambda

cat > .build/dummy-lambda/index.py <<'PY'
import json

def handler(event, context):
    return {
        "statusCode": 501,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps({
            "message": "Dummy lambda deployed."
        })
    }
PY

cd .build/dummy-lambda
zip -r ../dummy-lambda.zip index.py
cd -

aws s3 cp .build/dummy-lambda.zip \
  s3://${LAMBDA_ARTIFACTS_BUCKET}/Lambda-Artifacts/dummy-lambda/dummy-lambda.zip \
  --region ${AWS_REGION}


echo "Desplegando infra 02-data.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/02-data.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-data" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" EnablePointInTimeRecovery=false \
  --region "${AWS_REGION}"

echo "Desplegando infra 03-auth.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/03-auth.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-auth" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" CallbackUrl="${CALLBACK_URL}" LogoutUrl="${LOGOUT_URL}" \
  --region "${AWS_REGION}"

echo "Desplegando infra 04-messaging.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/04-messaging.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-messaging" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" \
  --region "${AWS_REGION}"

echo "Desplegando infra 05-lambdas-events.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/05-lambdas-events.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-lambdas-events" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" SenderEmail="${SENDER_EMAIL}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}"

echo "Desplegando infra 06-lambdas-registrations.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/06-lambdas-registrations.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-lambdas-registration" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" SenderEmail="${SENDER_EMAIL}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}"

echo "Desplegando infra 07-lambdas-reports.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/07-lambdas-reports.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-lambdas-report" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" SenderEmail="${SENDER_EMAIL}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}"

echo "Desplegando infra 08-lambdas-notifications.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/08-lambdas-notifications.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-lambdas-notifications" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" SenderEmail="${SENDER_EMAIL}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}"

echo "Desplegando infra 09-api.yaml..."

aws cloudformation deploy \
  --template-file "${ROOT_DIR}/infra/09-api.yaml" \
  --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-compute-api" \
  --parameter-overrides ProjectName="${PROJECT_NAME}" Environment="${ENVIRONMENT}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}"

