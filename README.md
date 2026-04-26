# Serverless Event Manager - AWS CloudFormation IaC

IaC inicial para un MVP de gestión de eventos serverless en AWS, optimizado para operar con servicios administrados.

## Qué despliega este MVP

| Stack | Archivo | Recursos principales |
|---|---|---|
| 01 | `infra/01-storage.yaml` | S3 para frontend/lambda artifacts, S3 reports, S3 templates/media, CloudFront con OAC |
| 02 | `infra/02-data.yaml` | DynamoDB Events, Registrations, NotificationTemplates, Reports |
| 03 | `infra/03-auth.yaml` | Cognito User Pool, App Client, grupos `organizers` y `attendees` |
| 04 | `infra/04-messaging.yaml` | SQS FIFO reports + DLQ, SQS notifications + DLQ |
| 05 | `infra/05-lambdas-events.yaml` | Lambdas Python dummies para el CRUD de los eventos, roles IAM separados |
| 06 | `infra/06-lambdas-registrations.yaml` | Lambdas Python dummies para el registro de usuarios , roles IAM separados |
| 07 | `infra/07-lambdas-reports.yaml` | Lambdas Python dummies para la generacion de reportes, roles IAM separados |
| 08 | `infra/08-lambdas-notifications.yaml` | Lambdas Python dummies para envio de notificaciones, roles IAM separados, SES identity |
| 09 | `infra/09-api.yaml` | API Gateway HTTP API, JWT authorizer, EventBridge Scheduler, SES identity |

> Nota: las Lambdas quedan desplegadas como `Dummies`. Esto permite validar la infraestructura completa y luego reemplazar el código por los handlers reales en Python.

> Nota: El orden de despliegue es el mismo indicado en la enumeracion


## Después de desplegar

1. Verifica el email de SES si recibes el correo de verificación.
2. Crea usuarios de prueba en Cognito y asígnalos a los grupos `organizers` o `attendees`.
3. Obtén el endpoint del API:

```bash
aws cloudformation describe-stacks \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-compute-api \
  --query "Stacks[0].Outputs[?OutputKey=='HttpApiEndpoint'].OutputValue" \
  --output text \
  --region ${AWS_REGION}
```

4. Reemplaza los dummies de Lambda por código real en Python.

## Flujo objetivo

- `POST /events`: organizador crea evento y la Lambda programa recordatorios 24h/12h con EventBridge Scheduler.
- `GET /events`: asistentes ven solo eventos `ACTIVE`; organizadores pueden consultar historial.
- `POST /events/{eventId}/registrations`: registro atómico con `TransactWriteItems` y `ConditionExpression` para evitar sobrecupo.
- `DELETE /events/{eventId}/registrations/{userId}`: cancelación atómica y liberación de cupo si el evento sigue activo.
- `POST /reports`: API devuelve 202, encola en SQS FIFO y `GenerateReport` procesa async.
- `SendNotification`: consume SQS notifications y envía correos por SES.

## Documentación incluida

- `dynamodb-data-model.md`: patrones de acceso, PK/SK, GSI y ejemplos de items mejorados.
