#Importa para el manejo de JSON.
import json
#Importa para el manejo de variables de entorno y fechas.
import os
#Importa para el manejo de AWS DynamoDB y errores.
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError
#Inicializa el cliente de DynamoDB.
dynamodb = boto3.client("dynamodb")
#Obtiene los nombres de las tablas de DynamoDB desde las variables de entorno.
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]

#Función para formatear las respuestas HTTP.
def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }

#Función principal del Lambda para registrar la inscripción de un usuario a un evento.
def handler(event, context):
    try:
        #Obtiene el cuerpo de la solicitud, que puede ser un string JSON o un objeto ya parseado.
        body = event.get("body")
        #Si el cuerpo es un string, lo parsea a JSON. Si no hay cuerpo, utiliza el evento completo como datos de entrada.
        if isinstance(body, str):
            body = json.loads(body)
        elif body is None:
            body = event
        #Define los campos obligatorios para la inscripción.
        required_fields = [
            "eventId",
            "userId",
            "correo",
            "nombreCompleto"
        ]
        #Verifica si faltan campos obligatorios en el cuerpo de la solicitud.
        missing = [field for field in required_fields if not body.get(field)]
        #Si faltan campos, devuelve una respuesta con código 400 y un mensaje indicando los campos faltantes.
        if missing:
            return response(400, {
                "message": "Faltan campos obligatorios",
                "missingFields": missing
            })
        #Extrae los datos necesarios del cuerpo de la solicitud para registrar la inscripción.
        event_id = body["eventId"]
        user_id = body["userId"]
        registration_date = datetime.now(timezone.utc).isoformat()
        #Define el estado de la inscripción y el estado de asistencia, así como la clave de estado del evento para facilitar consultas futuras.
        status = "inscrito"
        attendance_status = "pendiente"
        #La clave de estado del evento combina el ID del evento y el estado de la inscripción para facilitar consultas futuras.
        event_status_key = f"{event_id}#{status}"
        #Realiza una transacción en DynamoDB para actualizar el número de cupos disponibles del evento y registrar la inscripción del usuario. La transacción asegura que ambas operaciones se realicen de manera atómica.
        dynamodb.transact_write_items(
            TransactItems=[
                {
                    #La operación de actualización reduce el número de cupos disponibles en el evento. La condición asegura que el evento exista, esté abierto y tenga cupos disponibles.
                    "Update": {
                        "TableName": EVENTS_TABLE,
                        "Key": {
                            "eventId": {"S": event_id}
                        },
                        #La expresión de actualización reduce el número de cupos disponibles en 1. La condición asegura que el evento exista, tenga cupos disponibles y esté en estado "abierto".
                        "UpdateExpression": "SET availableSlots = availableSlots - :one",
                        "ConditionExpression": "attribute_exists(eventId) AND availableSlots > :zero AND #status = :open",
                        "ExpressionAttributeNames": {
                            "#status": "status"
                        },
                        #Los valores de los atributos expresados en la expresión de actualización.
                        "ExpressionAttributeValues": {
                            ":one": {"N": "1"},
                            ":zero": {"N": "0"},
                            ":open": {"S": "abierto"}
                        }
                    }
                },
                {   #La operación de inserción agrega un nuevo registro de inscripción para el usuario. La condición asegura que el usuario no esté ya inscrito en el evento.
                    "Put": {
                        "TableName": REGISTRATIONS_TABLE,
                        "Item": {
                            "eventId": {"S": event_id},
                            "userId": {"S": user_id},
                            "registrationDate": {"S": registration_date},
                            "status": {"S": status},
                            "attendanceStatus": {"S": attendance_status},
                            "eventStatusKey": {"S": event_status_key},
                            "correo": {"S": body["correo"]},
                            "nombreCompleto": {"S": body["nombreCompleto"]}
                        },
                        #La condición de inserción asegura que no exista ya un registro con el mismo eventId y userId, lo que garantiza que un usuario no pueda inscribirse más de una vez en el mismo evento.
                        "ConditionExpression": "attribute_not_exists(eventId) AND attribute_not_exists(userId)"
                    }
                }
            ]
        )
        #Si la transacción se completa con éxito, devuelve una respuesta con código 201 y un mensaje indicando que la inscripción se registró correctamente, junto con los detalles de la inscripción.
        return response(201, {
            "message": "Inscripción registrada correctamente",
            "data": {
                "eventId": event_id,
                "userId": user_id,
                "registrationDate": registration_date,
                "status": status,
                "attendanceStatus": attendance_status,
                "eventStatusKey": event_status_key,
                "correo": body["correo"],
                "nombreCompleto": body["nombreCompleto"]
            }
        })
    #Manejo de errores específicos de DynamoDB. Si ocurre un error durante la transacción, se captura el código de error para proporcionar mensajes de error más específicos. En caso de una "TransactionCanceledException", se analizan las razones de cancelación para determinar si el evento no existe, está cerrado, no tiene cupos disponibles o si el usuario ya está inscrito en el evento.
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        #Si la transacción fue cancelada, se analizan las razones de cancelación para proporcionar mensajes de error específicos. Si la primera razón indica que la condición de actualización falló, significa que el evento no existe, está cerrado o no tiene cupos disponibles. Si la segunda razón indica que la condición de inserción falló, significa que el usuario ya está inscrito en el evento.
        if error_code == "TransactionCanceledException":

            reasons = e.response.get("CancellationReasons", [])

            #Verifica las razones de cancelación para proporcionar mensajes de error específicos. Si la primera razón indica que la condición de actualización falló, significa que el evento no existe, está cerrado o no tiene cupos disponibles. Si la segunda razón indica que la condición de inserción falló, significa que el usuario ya está inscrito en el evento.
            if len(reasons) > 0 and reasons[0].get("Code") == "ConditionalCheckFailed":
                return response(400, {
                    "message": "El evento no existe, está cerrado o no tiene cupos disponibles."
                })

            #Verifica si la segunda razón indica que la condición de inserción falló
            if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                return response(409, {
                    "message": "El usuario ya está inscrito en este evento."
                })
            #Si no se pueden determinar las razones específicas de la cancelación, devuelve un mensaje genérico de error de inscripción.
            return response(409, {
                "message": "No se pudo completar la inscripción."
            })
        #Para otros errores de DynamoDB, devuelve un mensaje genérico de error de inscripción junto con el mensaje de error específico.
        return response(500, {
            "message": "Error al registrar la inscripción",
            "error": str(e)
        })
    #Manejo de cualquier otro error inesperado que pueda ocurrir durante la ejecución del Lambda. Devuelve un mensaje genérico de error interno junto con el mensaje de error específico.
    except Exception as e:
        return response(500, {
            "message": "Error interno",
            "error": str(e)
        })