#importaciones necesarias para manejar la generación de reportes, interacción con DynamoDB, S3 y Lambda
import json
import os
import csv
import uuid
from datetime import datetime, timezone
from io import StringIO
import boto3
#Clientes de AWS
dynamodb = boto3.client("dynamodb")
s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")
#Variables de entorno
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]
REPORTS_TABLE = os.environ["REPORTS_TABLE"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]

#Hardcode de la Lambda SendReport
SEND_REPORT_FUNCTION = "event-manager-dev-SendReport"


def handler(event, context):
    try:
        for record in event["Records"]:
            message = json.loads(record["body"])

            report_id = message.get("reportId", f"RPT-{str(uuid.uuid4())[:8].upper()}")
            report_type = message.get("reportType", "inscripciones")
            requested_by = message.get("requestedBy", "admin")
            filters = message.get("filters", {})

            event_id = filters.get("eventId")

            if not event_id:
                raise ValueError("El filtro eventId es obligatorio para generar el reporte.")

            #Consulta DynamoDB
            registrations = get_registrations_by_event(event_id)

            #Genera CSV
            csv_content = generate_csv(registrations)

            file_key = f"reports/{report_id}.csv"

            #Guarda en S3
            s3.put_object(
                Bucket=REPORTS_BUCKET,
                Key=file_key,
                Body=csv_content.encode("utf-8-sig"),
                ContentType="text/csv"
            )

            now = datetime.now(timezone.utc).isoformat()

            #Guarda metadata del reporte
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

            #Invoca SendReport
            lambda_client.invoke(
                FunctionName=SEND_REPORT_FUNCTION,
                InvocationType="Event",
                Payload=json.dumps({
                    "reportId": report_id,
                    "s3Bucket": REPORTS_BUCKET,
                    "s3Key": file_key,
                    "requestedBy": requested_by
                }).encode("utf-8")
            )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Reporte generado correctamente"
            }, ensure_ascii=False)
        }

    except Exception as e:
        print("Error generando reporte:", str(e))
        raise e


#Consulta por eventId
def get_registrations_by_event(event_id):
    response = dynamodb.query(
        TableName=REGISTRATIONS_TABLE,
        KeyConditionExpression="eventId = :eventId",
        ExpressionAttributeValues={
            ":eventId": {"S": event_id}
        }
    )

    return response.get("Items", [])


#Genera CSV
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