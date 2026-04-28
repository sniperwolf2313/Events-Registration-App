#Importa para manejar JSON
import json
#Importa para manejar variables de entorno
import os
#Importa para interactuar con DynamoDB
import boto3
#Importa para manejar errores de AWS
from botocore.exceptions import ClientError
#Crea un cliente de DynamoDB
dynamodb = boto3.client("dynamodb")
#Obtiene los nombres de las tablas desde las variables de entorno
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]
#Función para formatear la respuesta HTTP
def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }

#Función principal del Lambda para cancelar la inscripción a un evento
def handler(event, context):
    try:
        #Obtiene el cuerpo de la solicitud, que puede ser un string JSON o un objeto ya parseado
        body = event.get("body")
        #Si el cuerpo es un string, lo parsea a JSON. Si es None, utiliza el evento completo como cuerpo (para pruebas locales)
        if isinstance(body, str):
            body = json.loads(body)
        elif body is None:
            body = event
        #Valida que los campos obligatorios estén presentes en el cuerpo de la solicitud
        required_fields = ["eventId", "userId"]
        missing = [field for field in required_fields if not body.get(field)]
        #Si faltan campos obligatorios, devuelve un error 400 con un mensaje indicando qué campos faltan
        if missing:
            return response(400, {
                "message": "Faltan campos obligatorios",
                "missingFields": missing
            })
        #Obtiene el eventId y userId del cuerpo de la solicitud
        event_id = body["eventId"]
        user_id = body["userId"]
        #Realiza una transacción para eliminar la inscripción del usuario al evento y devolver el cupo al evento
        dynamodb.transact_write_items(
            TransactItems=[
                {#borra la inscripción del usuario al evento, con una condición que verifica que la inscripción exista antes de eliminarla
                    "Delete": {
                        "TableName": REGISTRATIONS_TABLE,
                        "Key": {
                            "eventId": {"S": event_id},
                            "userId": {"S": user_id}
                        },
                        "ConditionExpression": "attribute_exists(eventId) AND attribute_exists(userId)"
                    }
                },
                {#devuelve el cupo al evento incrementando availableSlots en 1, con una condición que verifica que el evento exista y que no se exceda la capacidad del evento
                    "Update": {
                        "TableName": EVENTS_TABLE,
                        "Key": {
                            "eventId": {"S": event_id}
                        },
                        "UpdateExpression": "SET availableSlots = availableSlots + :one",
                        "ConditionExpression": "attribute_exists(eventId) AND availableSlots < capacity",
                        "ExpressionAttributeValues": {
                            ":one": {"N": "1"}
                        }
                    }
                }
            ]
        )
        #Si La transacción se ejecuta correctamente, devuelve una respuesta 200 con un mensaje de éxito y los datos del evento y usuario
        return response(200, {
            "message": "Inscripción cancelada correctamente",
            "data": {
                "eventId": event_id,
                "userId": user_id
            }
        })
    #Si ocurre un error de cliente de AWS, maneja el error según el código de error específico y devuelve una respuesta adecuada
    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        if error_code == "TransactionCanceledException":
            reasons = e.response.get("CancellationReasons", [])

            if len(reasons) > 0 and reasons[0].get("Code") == "ConditionalCheckFailed":
                return response(404, {
                    "message": "El usuario no tiene una inscripción activa en este evento."
                })

            if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                return response(409, {
                    "message": "No se pudo devolver el cupo. El evento no existe o ya tiene todos los cupos disponibles."
                })

            return response(409, {
                "message": "No se pudo cancelar la inscripción."
            })

        return response(500, {
            "message": "Error al cancelar la inscripción",
            "error": str(e)
        })

    except Exception as e:
        return response(500, {
            "message": "Error interno",
            "error": str(e)
        })