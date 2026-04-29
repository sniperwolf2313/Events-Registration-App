#Importa para el manejo de JSON, variables de entorno, generación de UUIDs, manejo de fechas y el cliente de SQS de AWS SDK para Python (boto3).
import json
import os
from datetime import datetime, timezone
import uuid
#Crea un cliente de SQS utilizando boto3 para interactuar con la cola de mensajes.
import boto3
from botocore.exceptions import ClientError
#Obtiene la URL de la cola de reportes desde las variables de entorno, lo que permite una configuración flexible sin necesidad de modificar el código.
sqs = boto3.client("sqs")
#REPORTS_QUEUE_URL es una variable que almacena la URL de la cola de SQS donde se enviarán las solicitudes de generación de reportes. Esta URL se obtiene de las variables de entorno, lo que permite una configuración flexible sin necesidad de modificar el código.  
REPORTS_QUEUE_URL = os.environ["REPORTS_QUEUE_URL"]

#Función auxiliar para formatear las respuestas HTTP de manera consistente, incluyendo el código de estado, los encabezados y el cuerpo en formato JSON.
def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }

# Función principal del Lambda que maneja la creación de solicitudes de reportes. Recibe un evento y un contexto, procesa la solicitud para crear una nueva solicitud de reporte, valida los campos requeridos, genera un ID único para el reporte, envía el mensaje a la cola de SQS y devuelve una respuesta adecuada según el resultado de la operación.
def handler(event, context):
    try:
        #Intenta extraer el cuerpo de la solicitud, que puede ser una cadena JSON o un objeto ya parseado. Si el cuerpo es una cadena, se convierte a un diccionario utilizando json.loads. Si el cuerpo es None, se asigna el evento completo como el cuerpo.
        body = event.get("body")
        #Verifica que los campos requeridos "reportType" y "requestedBy" estén presentes en el cuerpo de la solicitud. Si faltan campos obligatorios, devuelve una respuesta con un código de estado 400 (Bad Request) indicando qué campos faltan.
        if isinstance(body, str):
            body = json.loads(body)
        elif body is None:
            body = event
        #Define una lista de campos requeridos para la solicitud de reporte y verifica si alguno de estos campos falta en el cuerpo de la solicitud. Si faltan campos obligatorios, devuelve una respuesta con un código de estado 400 (Bad Request) indicando qué campos faltan.
        required_fields = [
            "reportType",
            "requestedBy"
        ]
        #missing es una lista que contiene los nombres de los campos requeridos que no están presentes en el cuerpo de la solicitud. Se utiliza una comprensión de listas para iterar sobre los campos requeridos y verificar si cada campo está presente en el cuerpo de la solicitud utilizando body.get(field). Si un campo no está presente, se agrega a la lista missing.
        missing = [field for field in required_fields if not body.get(field)]
        #Si la lista missing no está vacía, significa que faltan campos obligatorios en la solicitud. En este caso, se devuelve una respuesta con un código de estado 400 (Bad Request) y un mensaje indicando que faltan campos obligatorios, junto con la lista de campos que faltan.
        if missing:
            return response(400, {
                "message": "Faltan campos obligatorios",
                "missingFields": missing
            })
        #Genera un ID único para el reporte utilizando uuid.uuid4() y formatea el ID con un prefijo "RPT-" seguido de
        report_id = f"RPT-{str(uuid.uuid4())[:8].upper()}"
        #Crea un mensaje que contiene la información de la solicitud de reporte, incluyendo el ID del reporte, el tipo de reporte, quién lo solicitó, los filtros opcionales, el estado inicial del reporte (pendiente) y la fecha de creación en formato ISO 8601. Este mensaje se envía a la cola de SQS para su procesamiento posterior.
        message = {
            "reportId": report_id,
            "reportType": body["reportType"],
            "requestedBy": body["requestedBy"],
            "filters": body.get("filters", {}),
            "status": "pendiente",
            "createdAt": datetime.now(timezone.utc).isoformat()
        }
        #Envía el mensaje a la cola de SQS utilizando el cliente de SQS. El mensaje se convierte a una cadena JSON utilizando json.dumps antes de enviarlo. Si el envío es exitoso, devuelve una respuesta con un código de estado 202 (Accepted) y un mensaje indicando que la solicitud de reporte fue recibida correctamente, junto con los datos del mensaje enviado.
        sqs.send_message(
            QueueUrl=REPORTS_QUEUE_URL,
            MessageBody=json.dumps(message, ensure_ascii=False)
        )
        #Si el envío es exitoso, devuelve una respuesta con un código de estado 202 (Accepted) y un mensaje indicando que la solicitud de reporte fue recibida correctamente, junto con los datos del mensaje enviado.
        return response(202, {
            "message": "Solicitud de reporte recibida correctamente",
            "data": message
        })
    #Si ocurre un error específico de SQS, como una excepción de ClientError, devuelve una respuesta con un código de estado 500 (Internal Server Error) y un mensaje indicando que hubo un error al enviar la solicitud de reporte a la cola, junto con los detalles del error.
    except ClientError as e:
        return response(500, {
            "message": "Error al enviar la solicitud de reporte a la cola",
            "error": str(e)
        })
    #Si ocurre cualquier otro tipo de excepción, devuelve una respuesta con un código de estado 500 (Internal Server Error) y un mensaje indicando que hubo un error interno, junto con los detalles del error.
    except Exception as e:
        return response(500, {
            "message": "Error interno",
            "error": str(e)
        })