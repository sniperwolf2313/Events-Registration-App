# Events Registration App — Manual de instalación, despliegue y pruebas

Sistema serverless para gestión de eventos en AWS. Permite crear y administrar eventos, registrar asistentes, cancelar registros, enviar notificaciones por correo y generar reportes asíncronos.

Este README está diseñado como una guía paso a paso para que una persona pueda clonar el repositorio, preparar su cuenta AWS, configurar GitHub Actions con OIDC, desplegar la infraestructura con CloudFormation y probar el sistema completo.

---

## 1. ¿En qué consiste el reto?

El reto consiste en diseñar, implementar y desplegar una aplicación de gestión de eventos utilizando servicios cloud de AWS e infraestructura como código.

La solución debe permitir:

- Autenticación de usuarios.
- Creación y consulta de eventos.
- Actualización y cancelación lógica de eventos.
- Registro de asistentes.
- Cancelación de registros.
- Control de cupos disponibles.
- Envío de notificaciones por correo.
- Generación de reportes en formato CSV.
- Despliegue automatizado de infraestructura y aplicación.

El entregable principal es este repositorio de código fuente. En él se incluyen las plantillas CloudFormation, el código fuente de las Lambdas, los scripts de despliegue, los workflows de GitHub Actions y esta documentación.

---

## 2. Repositorio

Repositorio del proyecto:

```text
https://github.com/sniperwolf2313/Events-Registration-App
```

Clonar el repositorio:

```bash
git clone https://github.com/sniperwolf2313/Events-Registration-App.git
cd Events-Registration-App
```

> Importante: todos los comandos de este README deben ejecutarse desde la raíz del repositorio, es decir, desde la carpeta `Events-Registration-App`, salvo que se indique lo contrario.

Validar ubicación actual:

```bash
pwd
```

La ruta debe terminar en:

```text
Events-Registration-App
```

---

## 3. Estructura del repositorio

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

### 3.1. Descripción de carpetas y archivos

| Ruta | Descripción |
|---|---|
| `.github/workflows/Deploy-Infra.yml` | Workflow de GitHub Actions para desplegar infraestructura con CloudFormation. |
| `.github/workflows/deploy-lambdas.yml` | Workflow de GitHub Actions para empaquetar y desplegar Lambdas. |
| `infra/` | Plantillas CloudFormation separadas por dominio técnico. |
| `Lambdas/` | Código fuente Python de las funciones Lambda. |
| `scripts/deploy-infra/validate.sh` | Script para validar las plantillas CloudFormation. |
| `scripts/deploy-infra/deploy.sh` | Script para desplegar los stacks de infraestructura desde la CLI. |
| `scripts/deploy-infra/delete.sh` | Script para eliminar los stacks de infraestructura. |
| `scripts/templates.sh` | Script para cargar plantillas de notificación en DynamoDB. |
| `README.md` | Guía principal de despliegue, configuración y pruebas. |

---

## 4. Arquitectura general

La solución usa una arquitectura serverless sobre AWS.

```text
Usuarios
  → Route 53 / CloudFront / S3 Frontend
  → Cognito User Pool
  → API Gateway HTTP API con JWT Authorizer
  → Lambdas Python 3.12
  → DynamoDB / SQS / EventBridge Scheduler / S3 / SES
```

### 4.1. Elementos principales usados

La arquitectura implementada usa los siguientes elementos:

```text
1 API Gateway HTTP API
1 Cognito User Pool
1 Cognito App Client
2 tablas DynamoDB principales
3 buckets S3 principales
2 colas SQS principales
8 Lambdas implementadas
1 grupo de schedules de EventBridge Scheduler
1 integración con Amazon SES
Roles IAM por dominio o función
CloudWatch Logs para observabilidad
CloudTrail para auditoría
KMS para cifrado cuando aplique
WAF y Shield considerados como servicios de protección en el diseño
```

### 4.2. Tablas DynamoDB

Las tablas principales son:

```text
event-manager-dev-events
event-manager-dev-registrations
event-manager-dev-notification-templates
```

La tabla `notification-templates` se usa para almacenar las plantillas dinámicas de correo consumidas por la Lambda `send-notification`.

### 4.3. Buckets S3 principales

Los buckets principales son:

```text
S3 Front / Artifacts
S3 Reports
S3 TemplatesFiles
```

Dependiendo de la plantilla `01-storage.yaml`, los nombres reales se generan usando el formato:

```text
<PROJECT_NAME>-<ENVIRONMENT>-<nombre-del-bucket>
```

Por ejemplo:

```text
event-manager-dev-front-artifacts
event-manager-dev-reports
event-manager-dev-templates-files
```

### 4.4. Lambdas implementadas

Las Lambdas finales de la solución son:

```text
cancel-register
create-event
create-report
generate-report
get-event
register-attendee
send-notification
update-event
```

Todas las Lambdas usan Python 3.12. Si el handler configurado en CloudFormation es `index.handler`, cada ZIP debe contener un archivo `index.py` en la raíz.

---

## 5. Ajustes frente al diseño inicial

Durante la implementación se simplificó la arquitectura inicial. El diseño planteado inicialmente incluía Lambdas adicionales para responsabilidades muy específicas. En la versión final se eliminaron dos Lambdas y sus responsabilidades fueron absorbidas por funciones existentes.

Las Lambdas eliminadas fueron:

```text
DeleteEvent
SendReport
```

### 5.1. Eliminación de DeleteEvent

La Lambda `DeleteEvent` fue eliminada porque se decidió no eliminar físicamente los eventos.

Su responsabilidad fue absorbida por:

```text
update-event
```

En la implementación final, cuando se ejecuta:

```text
DELETE /events/{eventId}
```

la operación se resuelve con la lógica de `update-event`, cambiando el estado del evento a:

```text
CANCELLED
```

También se cierra el registro de asistentes y se eliminan schedules pendientes relacionados con el evento.

Esta decisión permite:

- Mantener trazabilidad histórica.
- Evitar pérdida de información.
- Conservar consistencia con los registros asociados.
- Facilitar auditoría y reportes.

### 5.2. Eliminación de SendReport

La Lambda `SendReport` fue eliminada porque el envío de reportes no necesitaba una función independiente.

Su responsabilidad fue absorbida por el flujo compuesto por:

```text
create-report
generate-report
send-notification
```

El flujo final de reportes es:

```text
POST /reports
  → CreateReport
  → SQS reports
  → GenerateReport
  → S3 Reports
  → SQS notifications
  → SendNotification
  → SES
```

Esta decisión reduce la cantidad de Lambdas, evita duplicidad y mantiene el procesamiento de reportes de forma asíncrona.

---

## 6. Flujos funcionales principales

### 6.1. Gestión de eventos

```text
POST /events
  → CreateEvent
  → DynamoDB Events
  → EventBridge Scheduler crea:
      - start-status
      - end-status
      - reminder-24h
      - reminder-12h
```

### 6.2. Registro de asistentes

```text
POST /events/{eventId}/registrations
  → RegisterAttendee
  → DynamoDB transaction
  → Reduce availableSlots
  → Crea registro
  → SQS notifications
  → SendNotification
  → SES
```

### 6.3. Cancelación de registro

```text
DELETE /events/{eventId}/registrations/{userId}
  → CancelRegister
  → Valida que path userId == claims.sub
  → DynamoDB transaction
  → Aumenta availableSlots
  → Cambia registro a CANCELLED
  → SQS notifications
  → SendNotification
  → SES
```

### 6.4. Cancelación lógica de evento

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

### 6.5. Reportes

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

---

## 7. Requisitos previos

### 7.1. Herramientas locales

Instalar y validar:

```bash
git --version
aws --version
python3 --version
zip --version
bash --version
```

Se recomienda tener:

```text
Git
AWS CLI v2
Python 3.12 o superior
zip
bash
```

### 7.2. Cuenta AWS

La cuenta AWS debe tener permisos para crear y administrar:

```text
CloudFormation
IAM
S3
Cognito
API Gateway
Lambda
DynamoDB
SQS
EventBridge Scheduler
SES
CloudWatch Logs
CloudFront, si se despliega frontend
KMS, si las plantillas lo usan
```

---

## 8. Configurar AWS CLI local

Ejecutar:

```bash
aws configure
```

Ingresar:

```text
AWS Access Key ID: <access-key>
AWS Secret Access Key: <secret-key>
Default region name: us-east-1
Default output format: json
```

Validar identidad:

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

Guardar el Account ID en una variable:

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo $AWS_ACCOUNT_ID
```

Este valor se usa para crear roles, policies y configurar OIDC.

---

## 9. Variables para despliegue local

El script `scripts/deploy-infra/deploy.sh` ya incluye valores por defecto:

```bash
: "${AWS_REGION:=us-east-1}"
: "${PROJECT_NAME:=event-manager}"
: "${ENVIRONMENT:=dev}"
: "${SENDER_EMAIL:=sniperwolf2313@gmail.com}"
: "${CALLBACK_URL:=http://localhost:3000/callback}"
: "${LOGOUT_URL:=http://localhost:3000/logout}"
```

Esto significa que el script puede ejecutarse sin exportar variables previamente. Sin embargo, se recomienda definirlas explícitamente antes del despliegue, especialmente `SENDER_EMAIL`.

Desde la raíz del repositorio:

```bash
export AWS_REGION=us-east-1
export PROJECT_NAME=event-manager
export ENVIRONMENT=dev
export SENDER_EMAIL=<TU_CORREO_VERIFICABLE>
export CALLBACK_URL=http://localhost:3000/callback
export LOGOUT_URL=http://localhost:3000/logout
```

Ejemplo:

```bash
export AWS_REGION=us-east-1
export PROJECT_NAME=event-manager
export ENVIRONMENT=dev
export SENDER_EMAIL=mi-correo@example.com
export CALLBACK_URL=http://localhost:3000/callback
export LOGOUT_URL=http://localhost:3000/logout
```

### 9.1. ¿Para qué sirve cada variable?

| Variable | Uso | De dónde sale |
|---|---|---|
| `AWS_REGION` | Región donde se despliegan los recursos. | Se define manualmente. Recomendado: `us-east-1`. |
| `PROJECT_NAME` | Prefijo de nombres de recursos. | Se define manualmente. Recomendado: `event-manager`. |
| `ENVIRONMENT` | Ambiente de despliegue. | Se define manualmente. Recomendado: `dev`. |
| `SENDER_EMAIL` | Correo remitente para SES. | Debe ser un correo real al que tengas acceso. |
| `CALLBACK_URL` | URL de callback para Cognito Hosted UI. | Para pruebas locales: `http://localhost:3000/callback`. |
| `LOGOUT_URL` | URL de logout para Cognito Hosted UI. | Para pruebas locales: `http://localhost:3000/logout`. |


---

## 10. Validar plantillas CloudFormation

Antes de crear recursos, validar los templates:

```bash
bash scripts/deploy-infra/validate.sh
```

Este paso sirve para detectar errores de sintaxis o problemas básicos en los archivos YAML antes de intentar desplegar.

Si la validación es correcta, el script debe terminar sin errores.

---

## 11. Desplegar infraestructura localmente

Ejecutar desde la raíz del repositorio:

```bash
bash scripts/deploy-infra/deploy.sh
```

El script calcula automáticamente la raíz del proyecto usando:

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
```

Por esta razón, el script puede encontrar la carpeta `infra/`. Aun así, para evitar confusiones, se recomienda ejecutarlo siempre desde la raíz del repositorio.

### 11.1. Orden de despliegue

El script despliega los stacks en este orden:

```text
01-storage.yaml
02-data.yaml
03-auth.yaml
04-messaging.yaml
05-lambdas-events.yaml
06-lambdas-registrations.yaml
07-lambdas-reports.yaml
08-lambdas-notifications.yaml
09-api.yaml
```

### 11.2. ¿Por qué se despliega en ese orden?

| Orden | Template | Motivo |
|---|---|---|
| 1 | `01-storage.yaml` | Crea buckets S3 necesarios para artefactos, frontend, reportes o archivos base. |
| 2 | `02-data.yaml` | Crea tablas DynamoDB necesarias para eventos, registros y plantillas. |
| 3 | `03-auth.yaml` | Crea Cognito User Pool, App Client, dominio y grupos. |
| 4 | `04-messaging.yaml` | Crea colas SQS para reportes y notificaciones. |
| 5 | `05-lambdas-events.yaml` | Crea Lambdas relacionadas con eventos. |
| 6 | `06-lambdas-registrations.yaml` | Crea Lambdas relacionadas con registros. |
| 7 | `07-lambdas-reports.yaml` | Crea Lambdas relacionadas con reportes. |
| 8 | `08-lambdas-notifications.yaml` | Crea Lambda de notificaciones y sus integraciones. |
| 9 | `09-api.yaml` | Crea API Gateway, rutas, integraciones y authorizer. |

### 11.3. Nombres esperados de stacks

Con los valores por defecto, los stacks tendrán nombres similares a:

```text
event-manager-dev-storage
event-manager-dev-data
event-manager-dev-auth
event-manager-dev-messaging
event-manager-dev-lambdas-events
event-manager-dev-lambdas-registrations
event-manager-dev-lambdas-reports
event-manager-dev-lambdas-notifications
event-manager-dev-api
```

Validar stacks creados:

```bash
aws cloudformation list-stacks \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
  --region "$AWS_REGION" \
  --query "StackSummaries[?contains(StackName, '${PROJECT_NAME}-${ENVIRONMENT}')].[StackName,StackStatus]" \
  --output table
```

---

## 12. Obtener outputs importantes

Después de desplegar, varios valores deben tomarse desde los outputs de CloudFormation. No se deben hardcodear valores de otra cuenta AWS.

### 12.1. Outputs de storage

Consultar outputs del stack de storage:

```bash
aws cloudformation describe-stacks \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-storage \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

En estos outputs busca el bucket de artefactos de Lambdas o frontend. Según los nombres definidos en tu template, puede aparecer como:

```text
LambdaArtifactsBucket
ArtifactsBucketName
FrontArtifactsBucket
```

Exportar el valor:

```bash
export LAMBDA_ARTIFACTS_BUCKET=<VALOR_DEL_BUCKET_DE_ARTEFACTOS>
```

Ejemplo:

```bash
export LAMBDA_ARTIFACTS_BUCKET=event-manager-dev-front-artifacts
```

Este valor se usa especialmente en el workflow `deploy-lambdas.yml` para subir los ZIP de las Lambdas.

### 12.2. Outputs de Cognito

Consultar outputs del stack de autenticación:

```bash
aws cloudformation describe-stacks \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-auth \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

Buscar y exportar:

```bash
export USER_POOL_ID=<VALOR_DE_UserPoolId>
export APP_CLIENT_ID=<VALOR_DE_UserPoolClientId>
export COGNITO_DOMAIN=<VALOR_DE_UserPoolDomain>
```

No uses valores fijos como `us-east-1_xxxxx` o `21ff...`. Siempre toma estos valores desde los outputs del stack.

### 12.3. Outputs del API

Consultar outputs del stack de API:

```bash
aws cloudformation describe-stacks \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-api \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

Buscar el output de la URL del API. Puede aparecer como:

```text
ApiUrl
HttpApiUrl
ApiEndpoint
```

Exportar:

```bash
export API_URL=<VALOR_DEL_OUTPUT_API>
```

Ejemplo:

```bash
export API_URL=https://abc123.execute-api.us-east-1.amazonaws.com
```

---

## 13. Configurar SES

SES se usa para enviar correos de notificación.

Verificar el correo remitente:

```bash
aws ses verify-email-identity \
  --email-address "$SENDER_EMAIL" \
  --region "$AWS_REGION"
```

AWS enviará un correo de confirmación al correo definido en `SENDER_EMAIL`. Debes abrirlo y confirmar la verificación.

Validar estado:

```bash
aws ses get-identity-verification-attributes \
  --identities "$SENDER_EMAIL" \
  --region "$AWS_REGION"
```

El resultado esperado debe incluir:

```text
VerificationStatus: Success
```

Si SES está en sandbox, también debes verificar los correos destinatarios de prueba:

```bash
aws ses verify-email-identity \
  --email-address organizer@example.com \
  --region "$AWS_REGION"

aws ses verify-email-identity \
  --email-address attendee@example.com \
  --region "$AWS_REGION"
```

---

## 14. Cargar plantillas de notificación

La Lambda `send-notification` no usa plantillas hardcodeadas. Busca plantillas activas en DynamoDB.

El script encargado es:

```bash
scripts/templates.sh
```

Actualmente el script tiene esta estructura:

```bash
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${TEMPLATES_TABLE:=event-manager-dev-notification-templates}"
```

Esto significa que, si no se define nada, el script cargará las plantillas en:

```text
event-manager-dev-notification-templates
```

Si desplegaste con los valores por defecto:

```text
PROJECT_NAME=event-manager
ENVIRONMENT=dev
```

puedes ejecutar directamente:

```bash
bash scripts/templates.sh
```

Si cambiaste `PROJECT_NAME` o `ENVIRONMENT`, debes definir `TEMPLATES_TABLE` manualmente antes de ejecutar el script:

```bash
export TEMPLATES_TABLE=${PROJECT_NAME}-${ENVIRONMENT}-notification-templates
bash scripts/templates.sh
```

Ejemplo:

```bash
export AWS_REGION=us-east-1
export PROJECT_NAME=event-manager
export ENVIRONMENT=dev
export TEMPLATES_TABLE=event-manager-dev-notification-templates
bash scripts/templates.sh
```

Validar que las plantillas fueron cargadas:

```bash
aws dynamodb scan \
  --table-name "$TEMPLATES_TABLE" \
  --region "$AWS_REGION" \
  --limit 5
```

## 15. Configurar GitHub Actions con OIDC

GitHub Actions usa OIDC para asumir un role en AWS sin guardar access keys estáticas en GitHub.

### 15.1. Validar si existe el OIDC Provider

```bash
aws iam list-open-id-connect-providers
```

Si no existe un provider para:

```text
token.actions.githubusercontent.com
```

créalo:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

Validar nuevamente:

```bash
aws iam list-open-id-connect-providers
```

### 15.2. Crear trust policy

Define variables:

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export GITHUB_OWNER=<TU_USUARIO_O_ORGANIZACION>
export REPO_NAME=Events-Registration-App
```

Crear archivo `github-actions-trust-policy.json`:

```bash
cat > github-actions-trust-policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:${GITHUB_OWNER}/${REPO_NAME}:ref:refs/heads/*",
            "repo:${GITHUB_OWNER}/${REPO_NAME}:environment:dev"
          ]
        }
      }
    }
  ]
}
JSON
```

Crear role:

```bash
aws iam create-role \
  --role-name event-manager-github-actions-deploy-role \
  --assume-role-policy-document file://github-actions-trust-policy.json
```

Obtener ARN del role:

```bash
aws iam get-role \
  --role-name event-manager-github-actions-deploy-role \
  --query "Role.Arn" \
  --output text
```

Guarda este valor porque se usará en GitHub como:

```text
AWS_ROLE_ARN
```

### 15.3. Permisos del role

El role debe tener permisos para que CloudFormation pueda crear y actualizar los recursos definidos en las plantillas.

Para este laboratorio académico, el role debe permitir trabajar con:

```text
CloudFormation
IAM
S3
DynamoDB
Cognito
API Gateway
Lambda
SQS
EventBridge Scheduler
SES
CloudWatch Logs
CloudFront, si aplica
```

La forma más sencilla para la entrega es adjuntar una policy de despliegue al role.

Crear archivo `github-actions-deploy-policy.json`:

```bash
cat > github-actions-deploy-policy.json <<JSON
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
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStackResources",
        "cloudformation:CreateChangeSet",
        "cloudformation:DescribeChangeSet",
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
      "Sid": "DeployProjectResources",
      "Effect": "Allow",
      "Action": [
        "s3:*",
        "dynamodb:*",
        "lambda:*",
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
    },
    {
      "Sid": "ManageProjectRoles",
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
        "iam:ListAttachedRolePolicies",
        "iam:PassRole"
      ],
      "Resource": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/event-manager*"
    }
  ]
}
JSON
```

Crear la policy:

```bash
aws iam create-policy \
  --policy-name event-manager-github-actions-deploy-policy \
  --policy-document file://github-actions-deploy-policy.json
```

Adjuntar la policy al role:

```bash
aws iam attach-role-policy \
  --role-name event-manager-github-actions-deploy-role \
  --policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/event-manager-github-actions-deploy-policy
```

---

## 16. Variables necesarias en GitHub Actions

En GitHub entra a:

```text
Repository → Settings → Secrets and variables → Actions → Variables
```

Crea estas variables de repositorio:

| Variable | Ejemplo | Descripción | De dónde tomarla |
|---|---|---|---|
| `AWS_REGION` | `us-east-1` | Región de AWS donde se desplegará. | Debe coincidir con la región usada localmente. |
| `AWS_ROLE_ARN` | `arn:aws:iam::<account-id>:role/event-manager-github-actions-deploy-role` | Role que GitHub Actions asumirá por OIDC. | Sale del comando `aws iam get-role`. |
| `CALLBACK_URL` | `http://localhost:3000/callback` | URL de callback para Cognito. | Definida para pruebas locales o frontend real. |
| `LAMBDA_ARTIFACTS_BUCKET` | `event-manager-dev-front-artifacts` | Bucket donde el pipeline sube los ZIP de Lambdas. | Sale de outputs del stack `${PROJECT_NAME}-${ENVIRONMENT}-storage`. |
| `LOGOUT_URL` | `http://localhost:3000/logout` | URL de logout para Cognito. | Definida para pruebas locales o frontend real. |
| `PROJECT_NAME` | `event-manager` | Prefijo del proyecto. | Debe coincidir con el valor usado en CloudFormation. |
| `SENDER_EMAIL` | `mi-correo@example.com` | Email remitente de SES. | Debe ser un correo verificable en SES. |

> Nota: `ENVIRONMENT` no aparece como variable fija del repositorio porque en los workflows se recibe como input. Por ejemplo, al ejecutar el workflow se selecciona `environment = dev`.

### 16.1. Cómo usan estas variables los workflows

Los workflows cargan variables así:

```yaml
env:
  AWS_REGION: ${{ vars.AWS_REGION }}
  PROJECT_NAME: ${{ vars.PROJECT_NAME }}
  ENVIRONMENT: ${{ inputs.environment }}
  AWS_ROLE_ARN: ${{ vars.AWS_ROLE_ARN }}
```

Y luego configuran credenciales AWS con:

```yaml
role-to-assume: ${{ env.AWS_ROLE_ARN }}
aws-region: ${{ env.AWS_REGION }}
```

Esto significa que:

- `AWS_REGION` viene de las variables del repositorio.
- `PROJECT_NAME` viene de las variables del repositorio.
- `ENVIRONMENT` viene del input seleccionado al ejecutar el workflow.
- `AWS_ROLE_ARN` viene de las variables del repositorio.

### 16.2. Cómo obtener `LAMBDA_ARTIFACTS_BUCKET`

Después de desplegar el stack de storage, ejecutar:

```bash
aws cloudformation describe-stacks \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-storage \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

Busca el output del bucket de artefactos. Copia el valor y créalo en GitHub como variable:

```text
LAMBDA_ARTIFACTS_BUCKET
```

Si tu template no expone este output, puedes obtener el bucket listando buckets con el prefijo:

```bash
aws s3 ls | grep ${PROJECT_NAME}-${ENVIRONMENT}
```

---

## 17. Desplegar con GitHub Actions

### 17.1. Deploy de infraestructura

Workflow:

```text
.github/workflows/Deploy-Infra.yml
```

Ejecutar desde GitHub:

```text
Actions → Deploy Infra → Run workflow
```

Seleccionar:

```text
environment = dev
deploy_all = true
```

Este pipeline debe:

```text
Validar templates CloudFormation
Asumir el role AWS usando OIDC
Desplegar los stacks en orden
Mostrar errores si alguna plantilla falla
```

### 17.2. Deploy de Lambdas

Workflow:

```text
.github/workflows/deploy-lambdas.yml
```

Ejecutar desde GitHub:

```text
Actions → Deploy Lambdas → Run workflow
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

Para desplegar todas las Lambdas:

```text
lambda_name = all
```

El workflow usa `LAMBDA_ARTIFACTS_BUCKET` para subir los ZIP y actualizar el código de las funciones Lambda.

---

## 18. Validar ZIP de Lambdas

Cada Lambda usa handler:

```text
index.handler
```

Por eso el ZIP debe tener `index.py` en la raíz.

Validar un ZIP local:

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

Si el ZIP queda con una carpeta interna, Lambda no podrá encontrar el handler.

---

## 19. Crear usuarios de prueba en Cognito

Primero asegúrate de haber exportado:

```bash
echo $USER_POOL_ID
echo $APP_CLIENT_ID
```

### 19.1. Crear usuario organizador

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username organizer@example.com \
  --user-attributes Name=email,Value=organizer@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region "$AWS_REGION"

aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username organizer@example.com \
  --password 'Password123!' \
  --permanent \
  --region "$AWS_REGION"

aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$USER_POOL_ID" \
  --username organizer@example.com \
  --group-name organizers \
  --region "$AWS_REGION"
```

### 19.2. Crear usuario asistente

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username attendee@example.com \
  --user-attributes Name=email,Value=attendee@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region "$AWS_REGION"

aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username attendee@example.com \
  --password 'Password123!' \
  --permanent \
  --region "$AWS_REGION"

aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$USER_POOL_ID" \
  --username attendee@example.com \
  --group-name attendees \
  --region "$AWS_REGION"
```

Validar grupos:

```bash
aws cognito-idp admin-list-groups-for-user \
  --user-pool-id "$USER_POOL_ID" \
  --username organizer@example.com \
  --region "$AWS_REGION"

aws cognito-idp admin-list-groups-for-user \
  --user-pool-id "$USER_POOL_ID" \
  --username attendee@example.com \
  --region "$AWS_REGION"
```

---

## 20. Habilitar autenticación por usuario y contraseña

Para obtener tokens desde CLI, el App Client debe permitir `USER_PASSWORD_AUTH`.

Ejecutar:

```bash
aws cognito-idp update-user-pool-client \
  --user-pool-id "$USER_POOL_ID" \
  --client-id "$APP_CLIENT_ID" \
  --explicit-auth-flows "ALLOW_USER_PASSWORD_AUTH" "ALLOW_REFRESH_TOKEN_AUTH" \
  --region "$AWS_REGION"
```

No uses valores hardcodeados. Usa siempre `$USER_POOL_ID` y `$APP_CLIENT_ID` tomados desde los outputs de CloudFormation.

---

## 21. Obtener tokens de Cognito

### 21.1. Token del organizador

```bash
export ORGANIZER_TOKEN=$(aws cognito-idp initiate-auth \
  --client-id "$APP_CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=organizer@example.com,PASSWORD='Password123!' \
  --region "$AWS_REGION" \
  --query "AuthenticationResult.IdToken" \
  --output text)
```

Validar:

```bash
echo $ORGANIZER_TOKEN
```

### 21.2. Token del asistente

```bash
export ATTENDEE_TOKEN=$(aws cognito-idp initiate-auth \
  --client-id "$APP_CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=attendee@example.com,PASSWORD='Password123!' \
  --region "$AWS_REGION" \
  --query "AuthenticationResult.IdToken" \
  --output text)
```

Validar:

```bash
echo $ATTENDEE_TOKEN
```

Los endpoints deben recibir el token en el header:

```text
Authorization: Bearer <TOKEN>
```

### 21.3. Obtener el sub del asistente

Para cancelar un registro, el endpoint requiere el `userId`, que corresponde al claim `sub` del usuario.

```bash
export ATTENDEE_SUB=$(aws cognito-idp admin-get-user \
  --user-pool-id "$USER_POOL_ID" \
  --username attendee@example.com \
  --region "$AWS_REGION" \
  --query "UserAttributes[?Name=='sub'].Value" \
  --output text)

echo $ATTENDEE_SUB
```

---

## 22. Pruebas de API

Antes de probar, valida que existan estas variables:

```bash
echo $API_URL
echo $ORGANIZER_TOKEN
echo $ATTENDEE_TOKEN
```

### 22.1. Crear evento

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

Copia el `eventId` retornado y expórtalo:

```bash
export EVENT_ID=<EVENT_ID_RETORNADO>
```

Ejemplo:

```bash
export EVENT_ID=EVT-XXXXXXXX
```

### 22.2. Listar eventos

```bash
curl -X GET "$API_URL/events" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN"
```

### 22.3. Obtener evento por ID

```bash
curl -X GET "$API_URL/events/$EVENT_ID" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN"
```

### 22.4. Actualizar evento (No es necesario actualizar todos los campos en simultaneo)

```bash
curl -X PUT "$API_URL/events/$EVENT_ID" \
  -H "Authorization: Bearer $ORGANIZER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Prueba",
    "description": "Prueba Serverless",
    "location": "Auditorio principal",
    "startDate": "2026-05-05T19:00:00-05:00",
    "endDate": "2026-05-05T20:00:00-05:00",
    "capacity": 1
  }'
```

### 22.5. Registrar asistente

```bash
curl -X POST "$API_URL/events/$EVENT_ID/registrations" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "fullName": "Juan Pérez"
  }'
```

El sistema toma automáticamente:

```text
userId = claims.sub
email = claims.email
```

### 22.6. Cancelar registro

```bash
curl -X DELETE "$API_URL/events/$EVENT_ID/registrations/$ATTENDEE_SUB" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN"
```

`CancelRegister` valida que:

```text
pathParameters.userId == claims.sub
```

### 22.7. Solicitar reporte

```bash
curl -X POST "$API_URL/reports" \
  -H "Authorization: Bearer $ORGANIZER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "eventId": "'"$EVENT_ID"'",
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

### 22.8. Cancelar evento

```bash
curl -X DELETE "$API_URL/events/$EVENT_ID" \
  -H "Authorization: Bearer $ORGANIZER_TOKEN"
```

Resultado esperado:

```text
El evento queda con status CANCELLED.
```

---

## 23. Endpoints finales

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

## 24. Validaciones operativas

### 24.1. Validar stacks

```bash
aws cloudformation list-stacks \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
  --region "$AWS_REGION" \
  --query "StackSummaries[?contains(StackName, '${PROJECT_NAME}-${ENVIRONMENT}')].[StackName,StackStatus]" \
  --output table
```

### 24.2. Validar rutas de API Gateway

Primero obtener el API ID desde outputs o desde API Gateway:

```bash
aws apigatewayv2 get-apis \
  --region "$AWS_REGION" \
  --query "Items[*].[Name,ApiId,ApiEndpoint]" \
  --output table
```

Exportar:

```bash
export API_ID=<API_ID>
```

Validar rutas:

```bash
aws apigatewayv2 get-routes \
  --api-id "$API_ID" \
  --region "$AWS_REGION" \
  --query "Items[*].[RouteKey,Target]" \
  --output table
```

### 24.3. Validar DynamoDB

```bash
aws dynamodb list-tables \
  --region "$AWS_REGION" \
  --query "TableNames[?contains(@, '${PROJECT_NAME}-${ENVIRONMENT}')]"
```

Validar eventos:

```bash
aws dynamodb scan \
  --table-name ${PROJECT_NAME}-${ENVIRONMENT}-events \
  --region "$AWS_REGION" \
  --limit 5
```

Validar registros:

```bash
aws dynamodb scan \
  --table-name ${PROJECT_NAME}-${ENVIRONMENT}-registrations \
  --region "$AWS_REGION" \
  --limit 5
```

### 24.4. Validar schedules

```bash
aws scheduler list-schedules \
  --group-name ${PROJECT_NAME}-${ENVIRONMENT}-schedules \
  --region "$AWS_REGION" \
  --query "Schedules[*].[Name,State,Target.Arn]" \
  --output table
```

### 24.5. Validar reportes en S3

```bash
aws s3 ls s3://${PROJECT_NAME}-${ENVIRONMENT}-reports/reports/ --recursive
```

### 24.6. Validar logs

```bash
aws logs tail /aws/lambda/${PROJECT_NAME}-${ENVIRONMENT}-CreateEvent \
  --region "$AWS_REGION" \
  --since 15m

aws logs tail /aws/lambda/${PROJECT_NAME}-${ENVIRONMENT}-RegisterAttendee \
  --region "$AWS_REGION" \
  --since 15m

aws logs tail /aws/lambda/${PROJECT_NAME}-${ENVIRONMENT}-SendNotification \
  --region "$AWS_REGION" \
  --since 15m

aws logs tail /aws/lambda/${PROJECT_NAME}-${ENVIRONMENT}-GenerateReport \
  --region "$AWS_REGION" \
  --since 15m
```

---

## 25. Troubleshooting

### 25.1. GitHub Actions no puede asumir el role OIDC

Error:

```text
Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity
```

Revisar:

```text
AWS_ROLE_ARN correcto en GitHub Variables.
OIDC provider creado en AWS.
Trust policy con owner/repo correcto.
Si usas environment dev, incluir repo:<owner>/<repo>:environment:dev.
Workflow con permissions id-token: write.
```

### 25.2. CloudFormation indica que no hay cambios

No necesariamente es un error. Puede ocurrir cuando el stack ya existe y la plantilla no cambió.

En los workflows o scripts se puede usar:

```bash
--no-fail-on-empty-changeset
```

### 25.3. Error de template mayor a 51,200 bytes

CloudFormation tiene límites de tamaño para templates enviados directamente. Esta solución divide la infraestructura en varios archivos para evitar ese problema.

No se recomienda unir todos los recursos en una sola plantilla.

### 25.4. SES no envía correos

Revisar:

```text
SENDER_EMAIL verificado.
Destinatario verificado si SES está en sandbox.
Correo en spam/promociones.
Logs de SendNotification.
DLQ de notificaciones.
```

Ver logs:

```bash
aws logs tail /aws/lambda/${PROJECT_NAME}-${ENVIRONMENT}-SendNotification \
  --region "$AWS_REGION" \
  --since 30m
```

### 25.5. Error `KeyError: EVENTS_TABLE`

Significa que falta una variable de entorno en una Lambda.

Validar:

```bash
aws lambda get-function-configuration \
  --function-name ${PROJECT_NAME}-${ENVIRONMENT}-SendNotification \
  --region "$AWS_REGION" \
  --query "Environment.Variables"
```

### 25.6. API devuelve 401

Revisar:

```text
Token expirado.
Header Authorization mal formado.
Se está usando Access Token en vez de ID Token.
Authorizer de API Gateway mal configurado.
App Client incorrecto.
```

Formato correcto:

```text
Authorization: Bearer <ID_TOKEN>
```

### 25.7. API devuelve 403

Revisar:

```text
Usuario no pertenece al grupo correcto.
Endpoint requiere organizers y se está usando attendee.
Endpoint requiere attendees y se está usando organizer.
```

Validar grupos:

```bash
aws cognito-idp admin-list-groups-for-user \
  --user-pool-id "$USER_POOL_ID" \
  --username organizer@example.com \
  --region "$AWS_REGION"
```

### 25.8. `templates.sh` carga en una tabla incorrecta

Si cambiaste `PROJECT_NAME` o `ENVIRONMENT`, debes definir:

```bash
export TEMPLATES_TABLE=${PROJECT_NAME}-${ENVIRONMENT}-notification-templates
bash scripts/templates.sh
```

Si no lo haces, el script usará por defecto:

```text
event-manager-dev-notification-templates
```

---

## 26. Limpieza de recursos

Para eliminar los stacks:

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

Si algunos buckets tienen `DeletionPolicy: Retain`, deben vaciarse manualmente.

Listar buckets del proyecto:

```bash
aws s3 ls | grep ${PROJECT_NAME}-${ENVIRONMENT}
```

Vaciar buckets:

```bash
aws s3 rm s3://${PROJECT_NAME}-${ENVIRONMENT}-front-artifacts --recursive
aws s3 rm s3://${PROJECT_NAME}-${ENVIRONMENT}-reports --recursive
aws s3 rm s3://${PROJECT_NAME}-${ENVIRONMENT}-templates-files --recursive
```

Eliminar buckets:

```bash
aws s3api delete-bucket \
  --bucket ${PROJECT_NAME}-${ENVIRONMENT}-front-artifacts \
  --region "$AWS_REGION"

aws s3api delete-bucket \
  --bucket ${PROJECT_NAME}-${ENVIRONMENT}-reports \
  --region "$AWS_REGION"

aws s3api delete-bucket \
  --bucket ${PROJECT_NAME}-${ENVIRONMENT}-templates-files \
  --region "$AWS_REGION"
```

---
