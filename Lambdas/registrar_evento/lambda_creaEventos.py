#Para manejo de JSON
import json
#Para acceso a variables de entorno
import os
#Para generación de IDs únicos
import uuid
#Para manejo de fechas y horas
from datetime import datetime, timezone
#Para manejo de números decimales (si es necesario)
from decimal import Decimal
#para acceso a DynamoDB
import boto3
#Para manejo de errores específicos de AWS
from botocore.exceptions import ClientError
#Inicialización del cliente de DynamoDB y referencia a la tabla de eventos
dynamodb = boto3.resource("dynamodb")
#Referencia a la tabla de eventos usando el nombre definido en las variables de entorno
table = dynamodb.Table(os.environ["EVENTS_TABLE"])
#Función para formatear las respuestas HTTP de manera consistente
def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }

#Función principal del Lambda para manejar la creación de eventos
def handler(event, context):
    try:
        #El cuerpo de la solicitud puede venir como un string JSON o directamente como un diccionario, dependiendo de cómo se invoque el Lambda
        body = event.get("body")
        #Si el cuerpo es un string, se parsea a un diccionario. Si no se proporciona un cuerpo, se asume que los datos están directamente en el evento
        if isinstance(body, str):
            body = json.loads(body)
        elif body is None:
            body = event
        #Validación de campos obligatorios para la creación de un evento
        required_fields = [
            "title",
            "description",
            "location",
            "startDate",
            "endDate",
            "capacity",
            "createdBy"
        ]
        #Se verifica si faltan campos obligatorios en el cuerpo de la solicitud y se devuelve un error si es así
        missing = [field for field in required_fields if field not in body]

        if missing:
            return response(400, {
                "message": "Faltan campos obligatorios",
                "missingFields": missing
            })
        #
        capacity = int(body["capacity"])

        if capacity <= 0:
            return response(400, {
                "message": "La capacidad debe ser mayor a cero"
            })
        #Si no se proporciona un eventId, se genera uno automáticamente usando un prefijo y una parte de un UUID para garantizar que sea unico
        event_id = body.get("eventId", f"EVT-{str(uuid.uuid4())[:8].upper()}")
        #Se construye el item que se va a insertar en la tabla de DynamoDB con los datos del evento, incluyendo campos como título, descripción, ubicación, fechas, capacidad, etc. También se establece un valor predeterminado para availableSlots igual a la capacidad y un estado predeterminado de "abierto" si no se proporciona
        item = {
            "eventId": event_id,
            "title": body["title"],
            "description": body["description"],
            "location": body["location"],
            "startDate": body["startDate"],
            "endDate": body["endDate"],
            "capacity": capacity,
            "availableSlots": body.get("availableSlots", capacity),
            "status": body.get("status", "abierto"),
            "createdBy": body["createdBy"],
            "createdAt": datetime.now(timezone.utc).isoformat()
        }
        #Se intenta insertar el nuevo evento en la tabla de DynamoDB. Se utiliza una expresión de condición para asegurarse de que no exista ya un evento con el mismo eventId, lo que garantiza la unicidad del evento. Si la inserción es exitosa, se devuelve una respuesta con el código 201 y los detalles del evento creado
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(eventId)"
        )

        return response(201, {
            "message": "Evento registrado correctamente",
            "event": item
        })

    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(409, {
                "message": "Ya existe un evento con ese eventId"
            })

        return response(500, {
            "message": "Error al registrar el evento",
            "error": str(e)
        })

    except Exception as e:
        return response(500, {
            "message": "Error interno",
            "error": str(e)
        })