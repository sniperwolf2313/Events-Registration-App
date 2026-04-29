#importaciones necesarias para manejar la generación de reportes, interacción con DynamoDB, S3 y Lambda
import json
import os
#importaciones necesarias para manejar la generación de reportes, interacción con DynamoDB, S3 y Lambda
import boto3
#Clientes de AWS
dynamodb = boto3.client("dynamodb")
ses = boto3.client("ses")
#Variables de entorno
TEMPLATES_TABLE = os.environ["NOTIFICATION_TEMPLATES_TABLE"]
SES_SENDER_EMAIL = os.environ["SES_SENDER_EMAIL"]

#Función principal que maneja el evento de SQS, procesa el mensaje y envía la notificación por email utilizando SES
def handler(event, context):
    try:
        #Procesa cada mensaje recibido en el evento de SQS
        for record in event["Records"]:
            message = json.loads(record["body"])
            #Extrae información del mensaje para determinar el tipo de notificación, destinatario y contenido
            notification_type = message.get("type")
            recipient_email = message.get("recipientEmail")
            #Valida que se hayan proporcionado los campos necesarios para enviar la notificación
            if not notification_type or not recipient_email:
                raise ValueError("Los campos type y recipientEmail son obligatorios.")
            #Obtiene la plantilla de notificación desde DynamoDB según el tipo de notificación
            template = get_template(notification_type)
            #Reemplaza los placeholders en la plantilla con los valores específicos del mensaje
            subject = template["asunto"]
            body = template["contenido"]
            #Reemplaza placeholders en el contenido de la plantilla con los valores del mensaje
            body = body.replace("{nombre}", message.get("nombre", ""))
            body = body.replace("{titulo}", message.get("titulo", ""))
            body = body.replace("{reportId}", message.get("reportId", ""))
            body = body.replace("{s3Key}", message.get("s3Key", ""))
            #Envía el email utilizando SES con el asunto y cuerpo generados a partir de la plantilla
            ses.send_email(
                Source=SES_SENDER_EMAIL,
                Destination={
                    "ToAddresses": [recipient_email]
                },
                Message={
                    "Subject": {
                        "Data": subject,
                        "Charset": "UTF-8"
                    },
                    "Body": {
                        "Text": {
                            "Data": body,
                            "Charset": "UTF-8"
                        }
                    }
                }
            )
        #Retorna una respuesta exitosa después de procesar todos los mensajes
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Notificación enviada correctamente"
            }, ensure_ascii=False)
        }
    #Manejo de errores para capturar cualquier excepción que ocurra durante el procesamiento y envío de la notificación, registrando el error y lanzándolo nuevamente para su manejo posterior
    except Exception as e:
        print("Error enviando notificación:", str(e))
        raise e

#Función auxiliar para obtener la plantilla de notificación desde DynamoDB según el tipo de notificación, realizando una consulta utilizando un índice secundario global para filtrar por el tipo de plantilla y su estado activo
def get_template(notification_type):
    key_value = f"{notification_type}#true"
    #Realiza una consulta a DynamoDB para obtener la plantilla activa correspondiente al tipo de notificación, utilizando un índice secundario global para filtrar por el campo templateTypeStatus
    response = dynamodb.query(
        TableName=TEMPLATES_TABLE,
        IndexName="GSI1_TemplateTypeStatus",
        KeyConditionExpression="#tts = :value",
        ExpressionAttributeNames={
            "#tts": "templateTypeStatus"
        },
        ExpressionAttributeValues={
            ":value": {"S": key_value}
        }
    )
    #Obtiene los items resultantes de la consulta, verificando que exista al menos una plantilla activa para el tipo de notificación solicitado, y si es así, retorna el asunto y contenido de la plantilla para su uso en la generación del email
    items = response.get("Items", [])
    #Valida que exista al menos una plantilla activa para el tipo de notificación solicitado
    if not items:
        raise ValueError(f"No existe plantilla activa para: {notification_type}")
    #Retorna el asunto y contenido de la plantilla para su uso en la generación del email
    item = items[0]
    #Retorna el asunto y contenido de la plantilla para su uso en la generación del email
    return {
        "asunto": item["asunto"]["S"],
        "contenido": item["contenido"]["S"]
    }