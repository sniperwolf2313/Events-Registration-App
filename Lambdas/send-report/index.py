#Importación de las bibliotecas necesarias para el funcionamiento de la función Lambda. Se importan json para manejar datos en formato JSON, os para acceder a variables de entorno, datetime y timezone para manejar fechas y horas, y boto3 para interactuar con los servicios de AWS como S3, SQS y DynamoDB.
import json
import os
from datetime import datetime, timezone

import boto3
#Inicialización de los clientes de AWS para S3, SQS y DynamoDB. Se crean instancias de estos clientes utilizando boto3, lo que permite a la función Lambda interactuar con estos servicios para realizar operaciones como verificar la existencia de un archivo en S3, enviar mensajes a una cola de SQS y actualizar registros en DynamoDB.
s3 = boto3.client("s3")
sqs = boto3.client("sqs")
dynamodb = boto3.client("dynamodb")
#Definición de constantes para la tabla de reportes en DynamoDB, el bucket de S3 donde se almacenan los reportes generados y la URL de la cola de SQS para enviar notificaciones. Estas constantes se obtienen de las variables de entorno configuradas para la función Lambda, lo que permite una configuración flexible sin necesidad de modificar el código fuente.
REPORTS_TABLE = os.environ["REPORTS_TABLE"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
NOTIFICATIONS_QUEUE_URL = os.environ["NOTIFICATIONS_QUEUE_URL"]

#Función principal que maneja el evento de la función Lambda. Esta función se activa cuando se recibe un evento, generalmente desde otra función Lambda o un servicio de AWS. La función extrae la información necesaria del evento, como el ID del reporte, el bucket y la clave del archivo en S3, el correo electrónico del destinatario y quién solicitó el reporte. Luego, realiza validaciones para asegurarse de que los campos obligatorios estén presentes y que el archivo exista en S3. Si las validaciones son exitosas, se construye un mensaje de notificación con la información del reporte y se envía a la cola de SQS para que sea procesado por otra función Lambda encargada de enviar la notificación por correo electrónico. Finalmente, se actualiza el estado del reporte en DynamoDB para reflejar que la notificación ha sido enviada. Si ocurre algún error durante este proceso, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda.
def handler(event, context):
    try:
        report_id = event.get("reportId")
        s3_bucket = event.get("s3Bucket", REPORTS_BUCKET)
        s3_key = event.get("s3Key")
        requested_by = event.get("requestedBy", "admin")
        recipient_email = event.get("recipientEmail")

        #Validaciones
        if not report_id or not s3_key:
            raise ValueError("Los campos reportId y s3Key son obligatorios.")

        if not recipient_email:
            raise ValueError("El campo recipientEmail es obligatorio.")

        #Validar que el archivo existe en S3
        s3.head_object(
            Bucket=s3_bucket,
            Key=s3_key
        )

        now = datetime.now(timezone.utc).isoformat()

        #Mensaje para la cola notifications
        notification_message = {
            "type": "reporte",
            "recipientEmail": recipient_email,
            "reportId": report_id,
            "requestedBy": requested_by,
            "nombre": requested_by,
            "titulo": "Reporte generado correctamente",
            "s3Bucket": s3_bucket,
            "s3Key": s3_key,
            "createdAt": now
        }

        #Enviar mensaje a la cola de notificaciones para que sea procesado por la función SendNotification y se envíe el correo al usuario. Se utiliza el cliente de SQS para enviar un mensaje a la cola especificada, con el cuerpo del mensaje en formato JSON que contiene la información necesaria para enviar la notificación por correo electrónico. Si el envío es exitoso, se devuelve un mensaje de éxito con un código de estado 200. Si ocurre algún error durante el envío, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda.
        sqs.send_message(
            QueueUrl=NOTIFICATIONS_QUEUE_URL,
            MessageBody=json.dumps(notification_message, ensure_ascii=False)
        )

        #Actualizar estado del reporte en DynamoDB
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
        #Si todas las operaciones se realizan correctamente, la función devuelve un mensaje de éxito con un código de estado 200. Si ocurre algún error durante el proceso, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda.
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Reporte enviado a la cola de notificaciones correctamente",
                "reportId": report_id
            }, ensure_ascii=False)
        }

    except Exception as e:
        print("Error en SendReport:", str(e))
        raise e