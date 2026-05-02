#Importaciones necesarias para la función Lambda, incluyendo boto3 para interactuar con AWS y json para manejar los datos de entrada y salida. También se importan os para acceder a las variables de entorno.
import json
import os
import boto3
from boto3.dynamodb.conditions import Key

# Variables de entorno
TEMPLATES_TABLE = os.environ["NOTIFICATION_TEMPLATES_TABLE"]
SES_SENDER_EMAIL = os.environ["SES_SENDER_EMAIL"]

# Clientes AWS
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TEMPLATES_TABLE)

ses = boto3.client("ses")

    #Función principal que maneja el evento de la cola SQS. La función procesa cada mensaje recibido, extrayendo la información necesaria para enviar una notificación por correo electrónico utilizando SES. Se valida que los campos obligatorios estén presentes, se obtiene la plantilla correspondiente al tipo de notificación, se personaliza el contenido del correo y se envía utilizando SES. Si ocurre algún error durante el proceso, se captura y se imprime en los logs.

def handler(event, context):
    try:
        for record in event["Records"]:
            message = json.loads(record["body"])

            notification_type = message.get("type")
            recipient_email = message.get("recipientEmail")

            if not notification_type or not recipient_email:
                raise ValueError("Los campos type y recipientEmail son obligatorios.")

            template = get_template(notification_type)

            subject = template["asunto"]
            body = template["contenido"]

            body = body.replace("{nombre}", message.get("nombre", ""))
            body = body.replace("{titulo}", message.get("titulo", ""))
            body = body.replace("{reportId}", message.get("reportId", ""))
            body = body.replace("{s3Key}", message.get("s3Key", ""))
            #Se envía el correo electrónico utilizando el cliente de SES. Se especifica el remitente, el destinatario, el asunto y el cuerpo del mensaje en formato HTML. Si el envío es exitoso, se devuelve un mensaje de éxito con un código de estado 200. Si ocurre algún error durante el envío, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda.
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
                        "Html": {
                            "Data": body,
                            "Charset": "UTF-8"
                        }
                    }
                }
            )
        #Si todas las operaciones se realizan correctamente, la función devuelve un mensaje de éxito con un código de estado 200. Si ocurre algún error durante el proceso, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda.
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Notificación enviada correctamente"
            }, ensure_ascii=False)
        }
    #Si ocurre algún error durante el proceso, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda. Esto permite que cualquier error que ocurra durante el envío de la notificación sea registrado en los logs de CloudWatch y también permita que el entorno de ejecución de Lambda maneje la excepción según su configuración (por ejemplo, reintentos o notificaciones).
    except Exception as e:
        print("Error enviando notificación:", str(e))
        raise e

#Función auxiliar que consulta la tabla de DynamoDB para obtener la plantilla de notificación correspondiente al tipo de notificación especificado. La función realiza una consulta utilizando el método query del cliente de DynamoDB, especificando la tabla, el índice secundario global y la expresión de condición de clave para filtrar por el tipo de plantilla y su estado activo. Si se encuentra una plantilla activa para el tipo especificado, se devuelve un diccionario con el asunto y el contenido de la plantilla. Si no se encuentra ninguna plantilla activa, se lanza una excepción indicando que no existe una plantilla activa para ese tipo de notificación.
def get_template(notification_type):
    key_value = f"{notification_type}#true"

    response = table.query(
        IndexName="GSI1_TemplateTypeStatus",
        KeyConditionExpression=Key("templateTypeStatus").eq(key_value)
    )

    items = response.get("Items", [])

    if not items:
        raise ValueError(f"No existe plantilla activa para: {notification_type}")

    item = items[0]

    return {
        "asunto": item["asunto"],
        "contenido": item["contenido"]
    }