#Importa para el manejo de JSON, variables de entorno y el cliente de DynamoDB de AWS SDK para Python (boto3).
import json
import os
import boto3
from botocore.exceptions import ClientError
#Crea un cliente de DynamoDB utilizando boto3 para interactuar con la base de datos.
dynamodb = boto3.client("dynamodb")
#Obtiene el nombre de la tabla de eventos desde las variables de entorno, lo que permite una configuración flexible sin necesidad de modificar el código.
EVENTS_TABLE = os.environ["EVENTS_TABLE"]

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

#Función principal del Lambda que maneja la eliminación de eventos. Recibe un evento y un contexto, procesa la solicitud para eliminar un evento específico de la tabla DynamoDB y devuelve una respuesta adecuada según el resultado de la operación.
def handler(event, context):
    try:#Intenta extraer el cuerpo de la solicitud, que puede ser una cadena JSON o un objeto ya parseado. Si el cuerpo es una cadena, se convierte a un diccionario utilizando json.loads. Si el cuerpo es None, se asigna el evento completo como el cuerpo.
        body = event.get("body")
        #Verifica que el campo "eventId" esté presente en el cuerpo de la solicitud. Si no está presente, devuelve una respuesta con un código de estado 400 (Bad Request) indicando que el campo es obligatorio.
        if isinstance(body, str):
            body = json.loads(body)
        elif body is None:
            body = event
        #Si el campo "eventId" no está presente en el cuerpo, devuelve una respuesta con un código de estado 400 (Bad Request) indicando que el campo es obligatorio.
        if not body.get("eventId"):
            return response(400, {
                "message": "El campo eventId es obligatorio"
            })
        #Extrae el "eventId" del cuerpo de la solicitud y utiliza el cliente de DynamoDB para eliminar el ítem correspondiente en la tabla de eventos. La operación de eliminación se realiza con una condición que verifica que el ítem exista antes de eliminarlo, lo que ayuda a evitar errores al intentar eliminar un evento que no existe.
        event_id = body["eventId"]
        #Si la eliminación es exitosa, devuelve una respuesta con un código de estado 200 (OK) y un mensaje indicando que el evento fue eliminado correctamente, junto con el "eventId" del evento eliminado.
        dynamodb.delete_item(
            TableName=EVENTS_TABLE,
            Key={
                "eventId": {"S": event_id}
            },
            ConditionExpression="attribute_exists(eventId)"
        )
        #Si la eliminación es exitosa, devuelve una respuesta con un código de estado 200 (OK) y un mensaje indicando que el evento fue eliminado correctamente, junto con el "eventId" del evento eliminado.
        return response(200, {
            "message": "Evento eliminado correctamente",
            "eventId": event_id
        })
    #Si ocurre un error específico de DynamoDB, como una excepción de verificación condicional (ConditionalCheckFailedException) que indica que el evento no existe, devuelve una respuesta con un código de estado 404 (Not Found) y un mensaje indicando que el evento no existe. Para otros errores de DynamoDB, devuelve una respuesta con un código de estado 500 (Internal Server Error) y un mensaje indicando que hubo un error al eliminar el evento, junto con los detalles del error.
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        #Si el error es una excepción de verificación condicional, devuelve una respuesta con un código de estado 404 (Not Found) indicando que el evento no existe.
        if error_code == "ConditionalCheckFailedException":
            return response(404, {
                "message": "El evento no existe."
            })
        #Para otros errores de DynamoDB, devuelve una respuesta con un código de estado 500 (Internal Server Error) indicando que hubo un error al eliminar el evento, junto con los detalles del error.
        return response(500, {
            "message": "Error al eliminar el evento",
            "error": str(e)
        })
    #Si ocurre cualquier otro tipo de excepción, devuelve una respuesta con un código de estado 500 (Internal Server Error) y un mensaje indicando que hubo un error interno, junto con los detalles del error.
    except Exception as e:
        return response(500, {
            "message": "Error interno",
            "error": str(e)
        })