#Importa las librerías necesarias para el funcionamiento de la función Lambda. Estas incluyen: os 
#para acceder a las variables de entorno, json para manejar datos en formato JSON, csv para generar archivos CSV, uuid para generar identificadores únicos, datetime para manejar fechas y horas, StringIO para trabajar con cadenas como archivos, y boto3 para interactuar con los servicios de AWS como DynamoDB y S3.
import json
import os
import csv
import uuid
from datetime import datetime, timezone
from io import StringIO
import boto3
#Inicializa los clientes de DynamoDB y S3 utilizando boto3, lo que permitirá a la función Lambda interactuar con estos servicios para almacenar y recuperar datos.
dynamodb = boto3.client("dynamodb")
s3 = boto3.client("s3")
#Define las variables de entorno para las tablas de DynamoDB y el bucket de S3 que se utilizarán en la función Lambda. Estas variables se configuran en el entorno de ejecución de la función Lambda y permiten que el código sea más flexible y reutilizable.
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]
REPORTS_TABLE = os.environ["REPORTS_TABLE"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]

#La función handler es el punto de entrada principal para la función Lambda. Se encarga de procesar los eventos entrantes, generar el reporte en formato CSV, almacenarlo en S3 y registrar la información del reporte en DynamoDB. La función maneja cualquier excepción que pueda ocurrir durante este proceso y devuelve una respuesta adecuada.
def handler(event, context):
    try:
        #Itera sobre los registros recibidos en el evento, que se espera que sean mensajes de una cola SQS. Para cada mensaje, se extraen los detalles necesarios para generar el reporte, como el ID del reporte, el tipo de reporte, quién lo solicitó y los filtros aplicados. Luego, se obtiene la lista de inscripciones para el evento especificado, se genera un archivo CSV con esta información, se almacena en S3 y se registra la información del reporte en DynamoDB.
        for record in event["Records"]:
            message = json.loads(record["body"])
            #Genera un ID de reporte único si no se proporciona uno en el mensaje. El ID se formatea como "RPT-" seguido de los primeros 8 caracteres de un UUID generado aleatoriamente, lo que garantiza que cada reporte tenga un identificador único.
            report_id = message.get("reportId", f"RPT-{str(uuid.uuid4())[:8].upper()}")
            report_type = message.get("reportType", "inscripciones")
            requested_by = message.get("requestedBy", "admin")
            filters = message.get("filters", {})
            #Extrae el ID del evento del filtro proporcionado en el mensaje. Este ID es esencial para generar el reporte, ya que se utilizará para consultar las inscripciones relacionadas con ese evento específico. Si el ID del evento no se proporciona, se lanza una excepción indicando que es un filtro obligatorio.
            event_id = filters.get("eventId")
            #Si el ID del evento no se encuentra en los filtros, se lanza una excepción indicando que es un filtro obligatorio para generar el reporte. Esto asegura que la función Lambda tenga la información necesaria para generar el reporte correctamente.
            if not event_id:
                raise ValueError("El filtro eventId es obligatorio para generar el reporte.")
            #Obtiene las inscripciones relacionadas con el evento especificado utilizando la función get_registrations_by_event, que consulta la tabla de DynamoDB para recuperar los datos necesarios. Luego, se genera un archivo CSV con esta información utilizando la función generate_csv, que formatea los datos en un formato adecuado para su almacenamiento y uso posterior.
            registrations = get_registrations_by_event(event_id)
            #Genera el contenido del archivo CSV a partir de las inscripciones obtenidas. El contenido se codifica en UTF-8 para garantizar que se manejen correctamente los caracteres especiales. Luego, se define la clave del archivo en S3, que incluye la carpeta "reports" y el ID del reporte, lo que facilita la organización y recuperación de los archivos almacenados.
            csv_content = generate_csv(registrations)
            #Define la clave del archivo en S3, que incluye la carpeta "reports" y el ID del reporte. Esto permite organizar los archivos de reporte de manera estructurada dentro del bucket de S3, facilitando su acceso y gestión.
            file_key = f"reports/{report_id}.csv"
            #Almacena el archivo CSV generado en el bucket de S3 utilizando el método put_object del cliente de S3. El archivo se guarda con la clave definida anteriormente, y se especifica el tipo de contenido como "text/csv" para que sea reconocido correctamente como un archivo CSV.
            s3.put_object(
                Bucket=REPORTS_BUCKET,
                Key=file_key,
                Body=csv_content.encode("utf-8"),
                ContentType="text/csv"
            )
            #Registra la información del reporte en la tabla de DynamoDB utilizando el método put_item del cliente de DynamoDB. Se almacena el ID del reporte, el tipo de reporte, quién lo solicitó, el estado del reporte, la ubicación del archivo en S3 y las marcas de tiempo de creación y generación del reporte. Esto permite realizar un seguimiento de los reportes generados y facilita su gestión y recuperación en el futuro.
            now = datetime.now(timezone.utc).isoformat()
            #Almacena la información del reporte en DynamoDB, incluyendo el ID del reporte, el tipo de reporte, quién lo solicitó, el estado del reporte, la ubicación del archivo en S3 y las marcas de tiempo de creación y generación del reporte. Esto permite realizar un seguimiento de los reportes generados y facilita su gestión y recuperación en el futuro.
            dynamodb.put_item(
                TableName=REPORTS_TABLE,
                Item={
                    "reportId": {"S": report_id},
                    "reportType": {"S": report_type},
                    "requestedBy": {"S": requested_by},
                    "status": {"S": "generated"},
                    "s3Bucket": {"S": REPORTS_BUCKET},
                    "s3Key": {"S": file_key},
                    "createdAt": {"S": now},
                    "generatedAt": {"S": now}
                }
            )
        #Devuelve una respuesta indicando que el reporte se generó correctamente. La respuesta incluye un código de estado HTTP 200 y un mensaje en formato JSON que confirma la generación exitosa del reporte.
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Reporte generado correctamente"
            }, ensure_ascii=False)
        }
    #Si ocurre alguna excepción durante el proceso de generación del reporte, se captura la excepción, se imprime un mensaje de error en los logs y se vuelve a lanzar la excepción para que pueda ser manejada por el entorno de ejecución de Lambda. Esto asegura que cualquier error sea registrado adecuadamente y que la función Lambda pueda responder con un error si algo sale mal durante la generación del reporte.
    except Exception as e:
        print("Error generando reporte:", str(e))
        raise e

#La función get_registrations_by_event consulta la tabla de DynamoDB para obtener las inscripciones relacionadas con un evento específico. Utiliza el método query del cliente de DynamoDB, proporcionando el ID del evento como clave de consulta. La función devuelve una lista de inscripciones que coinciden con el ID del evento proporcionado.    
def get_registrations_by_event(event_id):
    response = dynamodb.query(
        TableName=REGISTRATIONS_TABLE,
        KeyConditionExpression="eventId = :eventId",
        ExpressionAttributeValues={
            ":eventId": {"S": event_id}
        }
    )
    #Devuelve la lista de inscripciones obtenidas de la consulta a DynamoDB. Si no se encuentran inscripciones para el evento especificado, se devuelve una lista vacía. Esto permite que la función Lambda maneje correctamente los casos en los que no hay inscripciones para un evento determinado.
    return response.get("Items", [])

#La función generate_csv toma una lista de inscripciones y genera un archivo CSV a partir de esta información. Utiliza la clase StringIO para crear un objeto de archivo en memoria, lo que permite escribir el contenido del CSV sin necesidad de crear un archivo físico en el sistema de archivos. La función escribe una fila de encabezado con los nombres de las columnas y luego itera sobre cada inscripción para escribir los datos correspondientes en el formato CSV. Finalmente, devuelve el contenido del CSV como una cadena.
def generate_csv(items):
    output = StringIO()
    #Crea un objeto de archivo en memoria utilizando StringIO, lo que permite escribir el contenido del CSV sin necesidad de crear un archivo físico en el sistema de archivos. Esto es útil para generar el CSV de manera eficiente y luego almacenarlo directamente en S3 sin tener que manejar archivos temporales.
    writer = csv.writer(output)
    #Escribe la fila de encabezado en el archivo CSV, que incluye los nombres de las columnas: "eventId", "userId", "nombreCompleto", "correo", "registrationDate", "status" y "attendanceStatus". Esto proporciona una estructura clara para el archivo CSV y facilita la interpretación de los datos cuando se visualiza o se procesa posteriormente.
    writer.writerow([
        "eventId",
        "userId",
        "nombreCompleto",
        "correo",
        "registrationDate",
        "status",
        "attendanceStatus"
    ])
    #Itera sobre cada inscripción en la lista de inscripciones y escribe una fila en el archivo CSV para cada una. Para cada inscripción, se extraen los valores correspondientes a las columnas definidas en el encabezado, utilizando el método get para manejar posibles valores faltantes. Esto asegura que el CSV se genere correctamente incluso si algunos campos no están presentes en las inscripciones.
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
    #Devuelve el contenido del archivo CSV como una cadena. El método getvalue() de StringIO se utiliza para obtener el contenido completo del archivo en memoria, lo que permite que la función Lambda almacene este contenido directamente en S3 sin necesidad de manejar archivos temporales en el sistema de archivos.
    return output.getvalue()