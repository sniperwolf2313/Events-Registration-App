# Events Registration App — Manual de instalación, despliegue y pruebas

Sistema serverless para gestión de eventos en AWS. Permite crear y administrar eventos, registrar asistentes, cancelar registros, enviar notificaciones por correo y generar reportes asíncronos.

Este README está pensado para que una persona con cero conocimiento previo pueda clonar el repositorio, preparar su cuenta AWS, configurar GitHub Actions con OIDC, desplegar la infraestructura con CloudFormation y probar el sistema completo.

---

## 1. Arquitectura general

La solución usa una arquitectura serverless:

```text
Usuarios
  → CloudFront / S3 Frontend
  → Cognito User Pool
  → API Gateway HTTP API con JWT Authorizer
  → Lambdas Python 3.12
  → DynamoDB / SQS / EventBridge Scheduler / S3 / SES
```

### Flujos principales

#### Gestión de eventos

```text
POST /events
  → CreateEvent
  → DynamoDB Events
  → EventBridge Scheduler crea:
      - start-status     → UpdateEvent
      - end-status       → UpdateEvent
      - reminder-24h     → SQS notifications
      - reminder-12h     → SQS notifications
```

#### Registro de asistentes

```text
POST /events/{eventId}/registrations
  → RegisterAttendee
  → DynamoDB transaction:
      - reduce availableSlots
      - create/update registration
  → SQS notifications
  → SendNotification
  → SES
```

#### Cancelación de registro

```text
DELETE /events/{eventId}/registrations/{userId}
  → CancelRegister
  → valida que path userId == claims.sub
  → DynamoDB transaction:
      - increase availableSlots
      - status = CANCELLED
  → SQS notifications
  → SendNotification
  → SES
```

#### Cancelación de evento

```text
DELETE /events/{eventId}
  → UpdateEvent
  → status = CANCELLED
  → registrationOpen = false
  → elimina schedules pendientes
  → SQS notifications EVENT_CANCELLED
  → SendNotification
  → SES
```

#### Reportes

```text
POST /reports
  → CreateReport
  → reportStatus = QUEUED
  → SQS reports FIFO
  → GenerateReport
  → genera CSV
  → guarda archivo en S3 Reports
  → SQS notifications REPORT_READY
  → SendNotification
  → SES
```

No se requiere trigger de S3 para el MVP. `GenerateReport` sube el archivo a S3 y luego encola la notificación de reporte listo.

---

## 2. Estructura del repositorio

```text
.
├── .github/
│   └── workflows/
│       ├── Deploy-Infra.yml
│       └── deploy-lambdas.yml
│
├── infra/
│   ├── 01-storage.yaml
│   ├── 02-data.yaml
│   ├── 03-auth.yaml
│   ├── 04-messaging.yaml
│   ├── 05-lambdas-events.yaml
│   ├── 06-lambdas-registrations.yaml
│   ├── 07-lambdas-reports.yaml
│   ├── 08-lambdas-notifications.yaml
│   └── 09-api.yaml
│
├── Lambdas/
│   ├── cancel-register/
│   ├── create-event/
│   ├── create-report/
│   ├── generate-report/
│   ├── get-event/
│   ├── register-attendee/
│   ├── send-notification/
│   └── update-event/
│
├── scripts/
│   ├── deploy-infra/
│   │   ├── delete.sh
│   │   ├── deploy.sh
│   │   └── validate.sh
│   ├── templates.sh
│   └── user-cognito
│
└── README.md
```

Notas de diseño:

- Todas las Lambdas usan Python 3.12.
- Si el handler en CloudFormation es `index.handler`, cada ZIP debe tener `index.py` en la raíz.

---

## 3. Requisitos previos

### 3.1. Herramientas locales

- Git
- AWS CLI v2
- Python 3.12 o superior
- zip
- bash

```

### 3.2. Cuenta AWS

Necesitas una cuenta AWS con permisos para crear:

- S3
- CloudFront
- ACM
- Cognito
- API Gateway
- Lambda
- IAM Roles
- DynamoDB
- SQS
- EventBridge Scheduler
- SES
- CloudWatch Logs
- KMS, si los templates lo usan

### 3.3. Repositorio GitHub

Necesitas permisos de administrador para configurar:

- GitHub Actions
- Repository variables
- GitHub Environments
- OIDC role ARN

---

## 4. Configurar AWS CLI local

Ejecuta:

```bash
aws configure
```

Ingresa:

```text
AWS Access Key ID: <access key temporal o de usuario administrador>
AWS Secret Access Key: <secret key>
Default region name: us-east-1
Default output format: json
```

Valida identidad:

```bash
aws sts get-caller-identity
```

Salida esperada:

```json
{
  "UserId": "...",
  "Account": "123456789012",
  "Arn": "arn:aws:iam::123456789012:user/..."
}
```

---

## 5. Configurar GitHub Actions con OIDC

OIDC permite que GitHub Actions asuma un role AWS sin guardar access keys en GitHub.

### 5.1. Crear el Identity Provider de GitHub en AWS

Primero valida si ya existe:

```bash
aws iam list-open-id-connect-providers
```

Si no existe `token.actions.githubusercontent.com`, créalo:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

Valida:

```bash
aws iam list-open-id-connect-providers
```

### 5.2. Crear trust policy

Crea el archivo `github-actions-trust-policy.json`:

```bash
cat > github-actions-trust-policy.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:<GITHUB_OWNER>/<REPO_NAME>:ref:refs/heads/*",
            "repo:<GITHUB_OWNER>/<REPO_NAME>:environment:dev"
          ]
        }
      }
    }
  ]
}
JSON
```

Reemplaza:

```text
<AWS_ACCOUNT_ID>  → tu cuenta AWS
<GITHUB_OWNER>    → tu usuario u organización GitHub
<REPO_NAME>       → nombre del repositorio
```

Ejemplo:

```text
repo:snipewolf2313/Events-Registration-App:ref:refs/heads/*
```

Crea el role:

```bash
aws iam create-role \
  --role-name event-manager-github-actions-deploy-role \
  --assume-role-policy-document file://github-actions-trust-policy.json
```

Guarda el ARN:

```bash
aws iam get-role \
  --role-name event-manager-github-actions-deploy-role \
  --query "Role.Arn" \
  --output text
```

### 5.3. Permisos del role de despliegue

Para laboratorio, usa una policy amplia porque CloudFormation debe crear recursos de muchos servicios.

Crea `github-actions-deploy-policy.json`:

```bash
cat > github-actions-deploy-policy.json <<'JSON'
{
	"Version": "2012-10-17",
	"Statement": [
		{
			"Sid": "CloudFormationDeploy",
			"Effect": "Allow",
			"Action": [
				"cloudformation:CreateStack",
				"cloudformation:UpdateStack",
				"cloudformation:DeleteStack",
				"cloudformation:DescribeEvents",
				"cloudformation:DescribeStacks",
				"cloudformation:DescribeStackEvents",
				"cloudformation:DescribeStackResources",
				"cloudformation:DescribeChangeSet",
				"cloudformation:CreateChangeSet",
				"cloudformation:ExecuteChangeSet",
				"cloudformation:DeleteChangeSet",
				"cloudformation:ValidateTemplate",
				"cloudformation:GetTemplate",
				"cloudformation:GetTemplateSummary",
				"cloudformation:ListExports"
			],
			"Resource": "*"
		},
		{
			"Sid": "S3ProjectBuckets",
			"Effect": "Allow",
			"Action": [
				"s3:CreateBucket",
				"s3:DeleteBucket",
				"s3:PutBucketPolicy",
				"s3:GetBucketPolicy",
				"s3:DeleteBucketPolicy",
				"s3:PutBucketVersioning",
				"s3:GetBucketVersioning",
				"s3:PutEncryptionConfiguration",
				"s3:PutLifecycleConfiguration",
				"s3:GetLifecycleConfiguration",
				"s3:PutBucketPublicAccessBlock",
				"s3:GetBucketPublicAccessBlock",
				"s3:PutBucketOwnershipControls",
				"s3:GetBucketOwnershipControls",
				"s3:PutBucketLogging",
				"s3:GetBucketLogging",
				"s3:ListBucket",
				"s3:GetBucketLocation",
				"s3:GetObject",
				"s3:PutObject",
				"s3:DeleteObject"
			],
			"Resource": [
				"arn:aws:s3:::event-manager*",
				"arn:aws:s3:::event-manager*/*"
			]
		},
		{
			"Sid": "DynamoDBProjectTables",
			"Effect": "Allow",
			"Action": [
				"dynamodb:CreateTable",
				"dynamodb:UpdateTable",
				"dynamodb:DeleteTable",
				"dynamodb:DescribeTable",
				"dynamodb:DescribeContinuousBackups",
				"dynamodb:UpdateContinuousBackups",
				"dynamodb:TagResource",
				"dynamodb:UntagResource",
				"dynamodb:ListTagsOfResource"
			],
			"Resource": [
				"arn:aws:dynamodb:*:367845900414:table/event-manager*",
				"arn:aws:dynamodb:*:367845900414:table/event-manager*/index/*"
			]
		},
		{
			"Sid": "LambdaProjectFunctions",
			"Effect": "Allow",
			"Action": [
				"lambda:CreateFunction",
				"lambda:UpdateFunctionCode",
				"lambda:UpdateFunctionConfiguration",
				"lambda:DeleteFunction",
				"lambda:GetFunction",
				"lambda:GetFunctionConfiguration",
				"lambda:AddPermission",
				"lambda:RemovePermission",
				"lambda:CreateEventSourceMapping",
				"lambda:UpdateEventSourceMapping",
				"lambda:DeleteEventSourceMapping",
				"lambda:GetEventSourceMapping",
				"lambda:ListEventSourceMappings",
				"lambda:TagResource",
				"lambda:UntagResource"
			],
			"Resource": [
				"arn:aws:lambda:*:367845900414:function:event-manager*",
				"arn:aws:lambda:*:367845900414:event-source-mapping:*"
			]
		},
		{
			"Sid": "IAMForProjectRoles",
			"Effect": "Allow",
			"Action": [
				"iam:CreateRole",
				"iam:DeleteRole",
				"iam:GetRole",
				"iam:UpdateRole",
				"iam:TagRole",
				"iam:UntagRole",
				"iam:PutRolePolicy",
				"iam:DeleteRolePolicy",
				"iam:GetRolePolicy",
				"iam:AttachRolePolicy",
				"iam:DetachRolePolicy",
				"iam:ListRolePolicies",
				"iam:ListAttachedRolePolicies"
			],
			"Resource": [
				"arn:aws:iam::367845900414:role/event-manager*"
			]
		},
		{
			"Sid": "PassProjectRolesToServices",
			"Effect": "Allow",
			"Action": "iam:PassRole",
			"Resource": [
				"arn:aws:iam::367845900414:role/event-manager*"
			],
			"Condition": {
				"StringEqualsIfExists": {
					"iam:PassedToService": [
						"lambda.amazonaws.com",
						"apigateway.amazonaws.com",
						"events.amazonaws.com",
						"scheduler.amazonaws.com"
					]
				}
			}
		},
		{
			"Sid": "ApiGatewayCognitoSqsSchedulerLogsSesCloudFront",
			"Effect": "Allow",
			"Action": [
				"apigateway:*",
				"cognito-idp:*",
				"sqs:*",
				"scheduler:*",
				"events:*",
				"logs:*",
				"ses:*",
				"cloudfront:*"
			],
			"Resource": "*"
		}
	]
}
JSON
```

Crear policy:

```bash
aws iam create-policy \
  --policy-name event-manager-github-actions-deploy-policy \
  --policy-document file://github-actions-deploy-policy.json
```

Adjuntar policy:

```bash
aws iam attach-role-policy \
  --role-name event-manager-github-actions-deploy-role \
  --policy-arn arn:aws:iam::<AWS_ACCOUNT_ID>:policy/event-manager-github-actions-deploy-policy
```

Para producción, reduce permisos y limita por prefijo `event-manager-*`.

---

## 6. Variables necesarias en GitHub

En GitHub:

```text
Repository → Settings → Secrets and variables → Actions → Variables
```

Crea:

| Variable | Ejemplo | Descripción |
|---|---|---|
| `AWS_REGION` | `us-east-1` | Región AWS |
| `PROJECT_NAME` | `event-manager` | Prefijo del proyecto |
| `AWS_ROLE_ARN` | `arn:aws:iam::<account>:role/event-manager-github-actions-deploy-role` | Role OIDC |
| `SENDER_EMAIL` | `<CORREO>` | Email remitente SES |
| `CALLBACK_URL` | `http://localhost:3000/callback` | Callback Cognito |
| `LOGOUT_URL` | `http://localhost:3000/logout` | Logout Cognito |


Reemplaza:

```text
<CORREO>  → correo electronico remitente, debes tener acceso para aprobar el uso.
```


## 7. Clonar el repositorio

```bash
git https://github.com/sniperwolf2313/Events-Registration-App.git
cd Events-Registration-App
```

Configura variables locales:

```bash
export AWS_REGION=us-east-1
export PROJECT_NAME=event-manager
export ENVIRONMENT=dev
export SENDER_EMAIL=<CORREO>
export CALLBACK_URL=http://localhost:3000/callback
export LOGOUT_URL=http://localhost:3000/logout
```

Reemplaza:

```text
<CORREO>  → correo electronico remitente, debes tener acceso para aprobar el uso.
```

## 8. Desplegar infraestructura localmente

### 8.1. Validar templates

```bash
bash scripts/deploy-infra/validate.sh
```


### 8.2. Desplegar

```bash
bash scripts/deploy-infra/deploy.sh
```

Orden esperado:

```text
01-storage
02-data
03-auth
04-messaging
05-lambdas-events
06-lambdas-registrations
07-lambdas-reports
08-lambdas-notifications
09-api
```

El stack `01-storage` crea el bucket donde se suben artefactos. Antes de crear Lambdas, debe existir un `dummy-lambda.zip` en:

```text
s3://<front-artifacts-bucket>/Lambda-Artifacts/dummy-lambda/dummy-lambda.zip
```

Esto permite crear Lambdas aunque el código real todavía no haya sido desplegado.

---

## 9. Desplegar con GitHub Actions

### 9.1. Deploy de infraestructura

Workflow:

```text
.github/workflows/Deploy-Infra.yml
```

Ejecutar:

```text
GitHub → Actions → Deploy Infra → Run workflow
```

Selecciona:

```text
environment = dev
deploy_all = true
```

Para cambios posteriores, despliega solo el stack modificado.

### 9.2. Deploy de Lambdas

Workflow:

```text
.github/workflows/deploy-lambdas.yml
```

Opciones esperadas:

```text
lambda_name = all
lambda_name = create-event
lambda_name = get-event
lambda_name = update-event
lambda_name = register-attendee
lambda_name = cancel-register
lambda_name = create-report
lambda_name = generate-report
lambda_name = send-notification
```

Si el handler es `index.handler`, el ZIP debe contener `index.py` en la raíz:

```bash
unzip -l .build/send-notification.zip
```

Correcto:

```text
index.py
```

Incorrecto:

```text
send-notification/index.py
```

---

## 10. Cargar plantillas de notificación

La Lambda `SendNotification` no usa templates hardcodeados. Busca templates activos en DynamoDB:

```text
event-manager-dev-notification-templates
```

La tabla usa:

```text
PK: templateId
GSI1_TemplateTypeStatus:
  PK: templateTypeStatus
  SK: updatedAt
```

Carga templates:

```bash
bash scripts/templates.sh
```

Templates esperados:

```text
REGISTRATION_CONFIRMATION#ACTIVE
REGISTRATION_CANCELLED#ACTIVE
EVENT_REMINDER_24H#ACTIVE
EVENT_REMINDER_12H#ACTIVE
EVENT_THANK_YOU#ACTIVE
EVENT_CANCELLED#ACTIVE
EVENT_UPDATED#ACTIVE
REPORT_READY#ACTIVE
```

Validar:

```bash
aws dynamodb query \
  --table-name event-manager-dev-notification-templates \
  --index-name GSI1_TemplateTypeStatus \
  --key-condition-expression "templateTypeStatus = :k" \
  --expression-attribute-values '{":k":{"S":"REGISTRATION_CONFIRMATION#ACTIVE"}}' \
  --region us-east-1
```

---

## 11. Configurar SES

Verifica el email remitente:

```bash
aws ses verify-email-identity \
  --email-address "$SENDER_EMAIL" \
  --region us-east-1
```

Abre el correo recibido y confirma la verificación.

Validar:

```bash
aws ses get-identity-verification-attributes \
  --identities "$SENDER_EMAIL" \
  --region us-east-1
```

Si SES está en sandbox, también debes verificar los correos destinatarios. En producción, solicita salida de sandbox desde la consola de SES.

---

## 12. Configurar Cognito y usuarios

### 12.1. Obtener outputs de Auth

```bash
aws cloudformation describe-stacks \
  --stack-name event-manager-dev-auth \
  --region us-east-1 \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

Guarda:

```text
UserPoolId
UserPoolClientId
UserPoolDomain
```

Define:

```bash
export USER_POOL_ID=<UserPoolId>
export APP_CLIENT_ID=<UserPoolClientId>
```

### 12.2. Crear usuario organizador

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username organizer@example.com \
  --user-attributes Name=email,Value=organizer@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region us-east-1

aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username organizer@example.com \
  --password 'Password123!' \
  --permanent \
  --region us-east-1

aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$USER_POOL_ID" \
  --username organizer@example.com \
  --group-name organizers \
  --region us-east-1
```

### 12.3. Crear usuario asistente

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username attendee@example.com \
  --user-attributes Name=email,Value=attendee@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region us-east-1

aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username attendee@example.com \
  --password 'Password123!' \
  --permanent \
  --region us-east-1

aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$USER_POOL_ID" \
  --username attendee@example.com \
  --group-name attendees \
  --region us-east-1
```

---

## 13. Obtener token de Cognito

### Opción A: CLI

Requiere que el App Client permita `USER_PASSWORD_AUTH`.

```bash
aws cognito-idp update-user-pool-client \
  --user-pool-id "us-east-1_a6a3VuRxz" \
  --client-id "21ffjkpdskoc2u5970fln0an53" \
  --explicit-auth-flows "ADMIN_NO_SRP_AUTH" "USER_PASSWORD_AUTH"
```

Organizador:

```bash
aws cognito-idp initiate-auth \
  --client-id "$APP_CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=organizer@example.com,PASSWORD='Password123!' \
  --region us-east-1
```

Asistente:

```bash
aws cognito-idp initiate-auth \
  --client-id "$APP_CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=attendee@example.com,PASSWORD='Password123!' \
  --region us-east-1
```


### Opción B: Hosted UI

Usa:

```text
https://<COGNITO_DOMAIN>/login?client_id=<APP_CLIENT_ID>&response_type=token&scope=openid+email+profile&redirect_uri=http://localhost:3000/callback
```

Después del login, copia el `id_token` desde la URL de redirección.

## 14. Obtener URL del API

```bash
aws cloudformation describe-stacks \
  --stack-name event-manager-dev-compute-api \
  --region us-east-1 \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

---

## 15. Pruebas de API

### Crear evento

```bash
curl -X POST "$API_URL/events" \
  -H "Authorization: Bearer $ORGANIZER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Prueba Serverless",
    "description": "Evento de prueba",
    "location": "Auditorio principal",
    "startDate": "2026-05-06T11:00:00-05:00",
    "endDate": "2026-05-06T12:00:00-05:00",
    "capacity": 50
  }'
```

La Lambda guarda fechas en UTC:

```text
2026-05-06T11:00:00-05:00 → 2026-05-06T16:00:00Z
```

### Listar eventos

```bash
curl -X GET "$API_URL/events" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN"
```

### Obtener evento

```bash
curl -X GET "$API_URL/events/EVT-XXXXXXXX" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN"
```

### Actualizar evento

```bash
curl -X PUT "$API_URL/events/EVT-XXXXXXXX" \
  -H "Authorization: Bearer $ORGANIZER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "location": "Auditorio 2",
    "notifyAttendees": true,
    "notificationMessage": "La ubicación fue actualizada."
  }'
```

### Cancelar evento

```bash
curl -X DELETE "$API_URL/events/EVT-XXXXXXXX" \
  -H "Authorization: Bearer $ORGANIZER_TOKEN"
```

También:

```bash
curl -X PUT "$API_URL/events/EVT-XXXXXXXX" \
  -H "Authorization: Bearer $ORGANIZER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status":"CANCELLED"}'
```

### Registrar asistente

```bash
curl -X POST "$API_URL/events/EVT-XXXXXXXX/registrations" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "fullName": "Juan Pérez"
  }'
```

El sistema toma:

```text
userId → claims.sub
email  → claims.email
```

### Cancelar registro

```bash
curl -X DELETE "$API_URL/events/EVT-XXXXXXXX/registrations/<USER_SUB>" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN"
```

`CancelRegister` valida que:

```text
pathParameters.userId == claims.sub
```

### Solicitar reporte

```bash
curl -X POST "$API_URL/reports" \
  -H "Authorization: Bearer $ORGANIZER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "eventId": "EVT-XXXXXXXX",
    "reportType": "EVENT_REGISTRATIONS",
    "format": "CSV"
  }'
```

Respuesta esperada:

```json
{
  "message": "Report request accepted.",
  "report": {
    "reportId": "RPT-XXXXXXXXXX",
    "reportStatus": "QUEUED"
  }
}
```

---

## 16. Endpoints finales

```text
POST   /events
GET    /events
GET    /events/{eventId}
PUT    /events/{eventId}
DELETE /events/{eventId}

POST   /events/{eventId}/registrations
DELETE /events/{eventId}/registrations/{userId}

POST   /reports
```

---

## 17. Validaciones operativas

### Stacks

```bash
aws cloudformation list-stacks \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
  --region us-east-1 \
  --query "StackSummaries[*].[StackName,StackStatus]" \
  --output table
```

### Rutas API Gateway

```bash
aws apigatewayv2 get-routes \
  --api-id <API_ID> \
  --region us-east-1 \
  --query "Items[*].[RouteKey,Target]" \
  --output table
```

### Integraciones API Gateway

```bash
aws apigatewayv2 get-integrations \
  --api-id <API_ID> \
  --region us-east-1 \
  --query "Items[*].[IntegrationId,IntegrationUri]" \
  --output table
```

### Event source mappings

```bash
aws lambda list-event-source-mappings \
  --function-name event-manager-dev-SendNotification \
  --region us-east-1 \
  --output table

aws lambda list-event-source-mappings \
  --function-name event-manager-dev-GenerateReport \
  --region us-east-1 \
  --output table
```

### Schedules

```bash
aws scheduler list-schedules \
  --group-name event-manager-dev-schedules \
  --region us-east-1 \
  --query "Schedules[*].[Name,State,Target.Arn]" \
  --output table
```

### S3 Reports

```bash
aws s3 ls s3://event-manager-dev-reports/reports/ --recursive
```

### Logs

```bash
aws logs tail /aws/lambda/event-manager-dev-CreateEvent --region us-east-1 --since 15m
aws logs tail /aws/lambda/event-manager-dev-RegisterAttendee --region us-east-1 --since 15m
aws logs tail /aws/lambda/event-manager-dev-SendNotification --region us-east-1 --since 15m
aws logs tail /aws/lambda/event-manager-dev-GenerateReport --region us-east-1 --since 15m
```

---

## 18. Troubleshooting

### OIDC no asume role

Error:

```text
Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity
```

Revisar:

```text
- AWS_ROLE_ARN correcto.
- OIDC provider creado.
- Trust policy con owner/repo correcto.
- Si usas GitHub environment, incluir environment:dev.
- Workflow con id-token: write.
```

### CloudFormation sin cambios

Usar:

```bash
--no-fail-on-empty-changeset
```

### Template mayor a 51,200 bytes

La solución divide la infraestructura en varios stacks pequeños. No concentres todo en un solo template grande.

### Buckets quedan después de borrar stacks

Algunos buckets usan `DeletionPolicy: Retain`.

Vaciar:

```bash
aws s3 rm s3://<bucket-name> --recursive
```

Eliminar:

```bash
aws s3api delete-bucket --bucket <bucket-name> --region us-east-1
```

### CloudFront muestra AccessDenied

Puede pasar si no existe `Frontend/index.html`.

Subir archivo temporal:

```bash
echo '<h1>Event Manager</h1>' > index.html
aws s3 cp index.html s3://event-manager-dev-front-artifacts/Frontend/index.html
```

### SendNotification cae en DLQ

Revisar:

```bash
aws logs tail /aws/lambda/event-manager-dev-SendNotification \
  --region us-east-1 \
  --since 30m
```

Causas comunes:

```text
- Falta dynamodb:GetItem sobre Events.
- Falta dynamodb:Query sobre Registrations index.
- Falta dynamodb:Query sobre NotificationTemplates index.
- Template no existe o no está ACTIVE.
- SES sender no verificado.
- SES sandbox y recipient no verificado.
```

### Error `KeyError: EVENTS_TABLE`

La Lambda no tiene variable de entorno.

Validar:

```bash
aws lambda get-function-configuration \
  --function-name event-manager-dev-SendNotification \
  --region us-east-1 \
  --query "Environment.Variables"
```

### Error CSS en HTML templates

Si aparece:

```text
Invalid format specifier ' margin: 0; padding: 0; ...'
```

La función de render no debe usar `format_map` directo sobre CSS. Debe reemplazar solo placeholders como:

```text
{fullName}
{title}
{eventId}
```

### SES acepta pero no llega correo

Si logs muestran:

```json
{"sent":1,"messageIds":["..."]}
```

SES aceptó el envío. Revisa:

```text
- Spam
- Promotions
- All Mail
- SES sandbox
- Email destinatario verificado
```

---

## 19. Limpieza de recursos

Borrar stacks:

```bash
bash scripts/deploy-infra/delete.sh
```

Orden recomendado:

```text
09-api
08-lambdas-notifications
07-lambdas-reports
06-lambdas-registrations
05-lambdas-events
04-messaging
03-auth
02-data
01-storage
```

Vaciar y eliminar buckets retenidos:

```bash
aws s3 ls | grep event-manager-dev
aws s3 rm s3://event-manager-dev-front-artifacts --recursive
aws s3 rm s3://event-manager-dev-reports --recursive
aws s3 rm s3://event-manager-dev-templates-files --recursive

aws s3api delete-bucket --bucket event-manager-dev-front-artifacts --region us-east-1
aws s3api delete-bucket --bucket event-manager-dev-reports --region us-east-1
aws s3api delete-bucket --bucket event-manager-dev-templates-files --region us-east-1
```

---

## 20. Criterios de evaluación cubiertos

### 1. Implementación completa — 25%

Cubierto por:

```text
- Gestión de eventos.
- Registro de asistentes.
- Cancelación de registros.
- Cancelación lógica de eventos.
- Control de cupos con transacciones DynamoDB.
- Notificaciones con SQS + SES.
- Reportes asíncronos con SQS + S3.
```

### 2. Automatización del despliegue — 25%

Cubierto por:

```text
- CloudFormation obligatorio.
- Stacks separados.
- GitHub Actions para infraestructura.
- GitHub Actions para Lambdas.
- OIDC sin access keys estáticas.
```

### 3. Calidad del código IaC — 25%

Cubierto por:

```text
- Stacks separados por responsabilidad.
- Parámetros ProjectName, Environment, SenderEmail.
- Outputs e ImportValue entre stacks.
- Roles IAM separados por Lambda.
- Uso de DLQ en SQS.
- Recursos con nombres consistentes.
```

### 4. Documentación — 25%

Cubierto por este README:

```text
- Requisitos previos.
- Configuración OIDC.
- Variables GitHub.
- Despliegue local.
- Despliegue por Actions.
- Configuración Cognito.
- Obtención de tokens.
- Pruebas de API.
- Troubleshooting.
- Limpieza de recursos.
```

---

## 21. Decisiones técnicas relevantes

### No usar DeleteEvent separada

`DELETE /events/{eventId}` apunta a `UpdateEvent`, que hace cancelación lógica:

```text
status = CANCELLED
registrationOpen = false
elimina schedules pendientes
encola EVENT_CANCELLED
```

### No usar SendReport en el MVP

`GenerateReport` sube el CSV a S3 y encola `REPORT_READY`.

### No usar attendanceStatus

El sistema no tiene validación real de asistencia. Por eso el agradecimiento se envía a registros con:

```json
{
  "status": "REGISTERED"
}
```

### Templates fuera del código

`SendNotification` consume HTML desde DynamoDB. Cambiar un correo no requiere redeploy de Lambda.

---

## 22. Estado esperado al finalizar

La solución se considera funcional cuando:

```text
- Un organizador crea un evento.
- El evento crea schedules automáticos.
- Un asistente lista eventos activos.
- Un asistente se registra.
- Se descuenta availableSlots.
- Se envía email de confirmación.
- Un asistente cancela su registro.
- Se libera availableSlots.
- Se envía email de cancelación de registro.
- Un organizador cancela el evento.
- Se eliminan schedules pendientes.
- Se envía email de cancelación del evento.
- Se solicita un reporte.
- CreateReport responde 202.
- GenerateReport crea CSV en S3.
- SendNotification envía el link por SES.
```
