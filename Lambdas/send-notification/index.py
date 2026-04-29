#Importaciones necesarias para manejar la generación de reportes, interacción con DynamoDB, S3 y SQS
import json
import os
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError
#Clientes de AWS
s3 = boto3.client("s3")
sqs = boto3.client("sqs")
dynamodb = boto3.client("dynamodb")
#Variables de entorno
REPORTS_TABLE = os.environ["REPORTS_TABLE"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
NOTIFICATIONS_QUEUE_URL = os.environ["NOTIFICATIONS_QUEUE_URL"]

#Función para formatear las respuestas de la Lambda
def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }

#Función principal de la Lambda para enviar notificaciones cuando un reporte está listo
def handler(event, context):
    try:
        report_id = event.get("reportId")
        s3_bucket = event.get("s3Bucket", REPORTS_BUCKET)
        s3_key = event.get("s3Key")
        requested_by = event.get("requestedBy", "admin")
        #Validación de campos obligatorios
        if not report_id or not s3_key:
            return response(400, {
                "message": "Los campos reportId y s3Key son obligatorios"
            })
        #Verifica que el archivo exista en S3 antes de enviar la notificación
        s3.head_object(
            Bucket=s3_bucket,
            Key=s3_key
        )
        #Si el archivo existe, envía la notificación a SQS
        now = datetime.now(timezone.utc).isoformat()
        #Construye el mensaje de notificación
        notification_message = {
            "type": "REPORT_READY",
            "reportId": report_id,
            "requestedBy": requested_by,
            "s3Bucket": s3_bucket,
            "s3Key": s3_key,
            "message": "El reporte fue generado correctamente y está disponible.",
            "createdAt": now
        }
        #Envía el mensaje a la cola de SQS
        sqs.send_message(
            QueueUrl=NOTIFICATIONS_QUEUE_URL,
            MessageBody=json.dumps(notification_message, ensure_ascii=False)
        )
        #Actualiza el estado del reporte en DynamoDB a "notification_sent"
        dynamodb.update_item(
            TableName=REPORTS_TABLE,
            Key={
                "reportId": {"S": report_id}
            },
            UpdateExpression="SET #status = :status, notificationSentAt = :sentAt",
            ExpressionAttributeNames={
                "#status": "status"
            },
            ExpressionAttributeValues={
                ":status": {"S": "notification_sent"},
                ":sentAt": {"S": now}
            }
        )
        #Retorna una respuesta exitosa con el mensaje de notificación
        return response(200, {
            "message": "Notificación enviada a la cola correctamente",
            "data": notification_message
        })
    #Manejo de errores específicos de AWS y errores generales
    except ClientError as e:
        return response(500, {
            "message": "Error al enviar la notificación del reporte",
            "error": str(e)
        })
    #Manejo de errores generales
    except Exception as e:
        return response(500, {
            "message": "Error interno",
            "error": str(e)
        })