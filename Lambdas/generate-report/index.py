#importación de las bibliotecas necesarias para la función Lambda. Se importan módulos para manejar JSON, interactuar con el sistema operativo, trabajar con CSV, generar identificadores únicos, manejar fechas y horas, y trabajar con flujos de texto. También se importan los clientes de AWS SDK para DynamoDB, S3 y Lambda.
import json
import os
import csv
import uuid
from datetime import datetime, timezone
from io import StringIO

import boto3
#Inicialización de los clientes de DynamoDB, S3 y Lambda para interactuar con estos servicios de AWS. Se definen las constantes para las tablas de DynamoDB, el bucket de S3 y el nombre de la función Lambda que se utilizará para enviar el reporte, que se obtienen de las variables de entorno.
dynamodb = boto3.client("dynamodb")
s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")
#Constantes para las tablas de DynamoDB, el bucket de S3 y el nombre de la función Lambda que se utilizará para enviar el reporte, que se obtienen de las variables de entorno.
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]
REPORTS_TABLE = os.environ["REPORTS_TABLE"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
#Constante para el nombre de la función Lambda que se utilizará para enviar el reporte, que se obtiene de las variables de entorno.
SEND_REPORT_FUNCTION = "event-manager-dev-SendReport"

#función principal del Lambda que se ejecuta cuando se recibe un evento. Procesa cada registro del evento, extrae la información necesaria para generar el reporte, obtiene las inscripciones correspondientes al evento especificado, genera un archivo CSV con los datos de las inscripciones, lo guarda en S3 y luego invoca otra función Lambda para enviar el reporte por correo electrónico. Si ocurre algún error durante el proceso, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda.   
def handler(event, context):
    try:#Itera sobre cada registro en el evento recibido, que se espera que contenga mensajes con la información necesaria para generar el reporte. Para cada mensaje, se extraen los campos necesarios, se obtiene las inscripciones correspondientes al evento especificado, se genera un archivo CSV con los datos de las inscripciones, se guarda en S3 y luego se invoca otra función Lambda para enviar el reporte por correo electrónico.
        for record in event["Records"]:
            message = json.loads(record["body"])

            report_id = message.get("reportId", f"RPT-{str(uuid.uuid4())[:8].upper()}")
            report_type = message.get("reportType", "inscripciones")
            requested_by = message.get("requestedBy", "admin")
            recipient_email = message.get("recipientEmail")
            filters = message.get("filters", {})

            event_id = filters.get("eventId")
            #Validación de los campos necesarios para generar el reporte. Se verifica que el campo eventId esté presente en los filtros, ya que es necesario para obtener las inscripciones correspondientes al evento. También se verifica que el campo recipientEmail esté presente, ya que es necesario para enviar el reporte por correo electrónico. Si alguno de estos campos falta, se lanza una excepción indicando que son obligatorios.
            if not event_id:
                raise ValueError("El filtro eventId es obligatorio para generar el reporte.")

            if not recipient_email:
                raise ValueError("El campo recipientEmail es obligatorio para enviar el reporte.")

            registrations = get_registrations_by_event(event_id)

            csv_content = generate_csv(registrations)

            file_key = f"reports/{report_id}.csv"
            #Se guarda el archivo CSV generado en S3 utilizando el método put_object del cliente de S3. Se especifica el bucket, la clave del archivo (que incluye el ID del reporte) y el contenido del archivo en formato CSV. También se establece el tipo de contenido como "text/csv" para que se reconozca correctamente al descargarlo.
            s3.put_object(
                Bucket=REPORTS_BUCKET,
                Key=file_key,
                Body=csv_content.encode("utf-8-sig"),
                ContentType="text/csv"
            )
            #Se obtiene la fecha y hora actual en formato ISO 8601 con zona horaria UTC para registrar cuándo se generó el reporte. Luego, se guarda un registro del reporte generado en la tabla de DynamoDB utilizando el método put_item del cliente de DynamoDB. Se especifica la tabla, la clave del item (reportId) y los atributos del reporte, incluyendo el tipo de reporte, quién lo solicitó, el correo del destinatario, el estado del reporte, la ubicación en S3 y las fechas de creación y generación.
            now = datetime.now(timezone.utc).isoformat()
            #Se guarda un registro del reporte generado en la tabla de DynamoDB utilizando el método put_item del cliente de DynamoDB. Se especifica la tabla, la clave del item (reportId) y los atributos del reporte, incluyendo el tipo de reporte, quién lo solicitó, el correo del destinatario, el estado del reporte, la ubicación en S3 y las fechas de creación y generación.
            dynamodb.put_item(
                TableName=REPORTS_TABLE,
                Item={
                    "reportId": {"S": report_id},
                    "reportType": {"S": report_type},
                    "requestedBy": {"S": requested_by},
                    "recipientEmail": {"S": recipient_email},
                    "status": {"S": "generated"},
                    "s3Bucket": {"S": REPORTS_BUCKET},
                    "s3Key": {"S": file_key},
                    "createdAt": {"S": now},
                    "generatedAt": {"S": now}
                }
            )
            #Se invoca la función Lambda encargada de enviar el reporte por correo electrónico utilizando el método invoke del cliente de Lambda. Se especifica el nombre de la función, el tipo de invocación (Event para invocación asíncrona) y el payload que contiene la información necesaria para enviar el reporte, incluyendo el ID del reporte, la ubicación en S3, quién lo solicitó y el correo del destinatario. El payload se codifica en formato JSON antes de enviarlo.
            lambda_client.invoke(
                FunctionName=SEND_REPORT_FUNCTION,
                InvocationType="Event",
                Payload=json.dumps({
                    "reportId": report_id,
                    "s3Bucket": REPORTS_BUCKET,
                    "s3Key": file_key,
                    "requestedBy": requested_by,
                    "recipientEmail": recipient_email
                }).encode("utf-8")
            )
        #Si todas las operaciones se realizan correctamente, la función devuelve un mensaje de éxito con un código de estado 200. Si ocurre algún error durante el proceso, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda.
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Reporte generado correctamente"
            }, ensure_ascii=False)
        }
    #Si ocurre algún error durante el proceso, se captura y se imprime en los logs, y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda. Esto permite que cualquier error que ocurra durante la generación del reporte sea registrado en los logs de CloudWatch y también permita que el entorno de ejecución de Lambda maneje la excepción según su configuración (por ejemplo, reintentos o notificaciones).
    except Exception as e:
        print("Error generando reporte:", str(e))
        raise e

#Función auxiliar que consulta la tabla de DynamoDB para obtener las inscripciones correspondientes a un evento específico. La función realiza una consulta utilizando el método query del cliente de DynamoDB, especificando la tabla, la expresión de condición de clave y los valores de los atributos necesarios para filtrar por el ID del evento. La función devuelve una lista de items que representan las inscripciones encontradas para el evento especificado.
def get_registrations_by_event(event_id):
    response = dynamodb.query(
        TableName=REGISTRATIONS_TABLE,
        KeyConditionExpression="eventId = :eventId",
        ExpressionAttributeValues={
            ":eventId": {"S": event_id}
        }
    )

    return response.get("Items", [])

#Función auxiliar que genera un archivo CSV a partir de una lista de items que representan las inscripciones. La función utiliza el módulo csv para escribir los datos en formato CSV, creando un flujo de texto en memoria utilizando StringIO. Se escribe una fila de encabezado con los nombres de las columnas, y luego se itera sobre cada item para escribir una fila con los valores correspondientes a cada columna. Finalmente, se devuelve el contenido del archivo CSV como una cadena de texto.
def generate_csv(items):
    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "eventId",
        "userId",
        "nombreCompleto",
        "correo",
        "registrationDate",
        "status",
        "attendanceStatus"
    ])
    #Se itera sobre cada item en la lista de inscripciones y se escribe una fila en el archivo CSV con los valores correspondientes a cada columna. Se utiliza el método get para extraer los valores de cada campo del item, proporcionando un valor predeterminado vacío en caso de que el campo no exista. Esto asegura que el archivo CSV se genere correctamente incluso si algunos campos están ausentes en los items.
    for item in items:
        writer.writerow([
            item.get("eventId", {}).get("S", ""),
            item.get("userId", {}).get("S", ""),
            item.get("nombreCompleto", {}).get("S", ""),
            item.get("correo", {}).get("S", ""),
            item.get("registrationDate", {}).get("S", ""),
            item.get("status", {}).get("S", ""),
            item.get("attendanceStatus", {}).get("S", "")
        ])

    return output.getvalue()